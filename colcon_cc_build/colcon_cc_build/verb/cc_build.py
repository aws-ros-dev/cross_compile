from collections import namedtuple
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile


from colcon_core.logging import colcon_logger
from colcon_core.plugin_system import satisfies_version
from colcon_core.verb import VerbExtensionPoint
from colcon_core.verb.build import BuildVerb
import docker

logger = colcon_logger.getChild(__name__)
_exit_codes = {'default': 1}

_CC_BUILD_SETUP_FILE_TEMPLATE = """
if [ -d {ros_root} ]
then
    source {ros_root}/setup.bash
else
    echo "WARNING: no ROS distro found on the sysroot"
fi

export TARGET_ARCH={target_arch}
export TARGET_TRIPLE={target_triple}
export CC_ROOT={cc_root}
"""

_CC_BUILD_SYSTEM_SETUP_SCRIPT_TEMPLATE = """
sudo rm -f /lib/{target_triple}
sudo ln -s {cc_root}/sysroot/lib/{target_triple} /lib/{target_triple}
sudo rm -f /usr/lib/{target_triple}
sudo ln -s {cc_root}/sysroot/usr/lib/{target_triple} /usr/lib/{target_triple}

CROSS_COMPILER_LIB=/usr/{target_triple}/lib
CROSS_COMPILER_LIB_BAK=${{CROSS_COMPILER_LIB}}_$(date +%s).bak
echo "Backing up ${{CROSS_COMPILER_LIB}} to ${{CROSS_COMPILER_LIB_BAK}}"
sudo mv ${{CROSS_COMPILER_LIB}} ${{CROSS_COMPILER_LIB_BAK}}
sudo ln -s {cc_root}/sysroot/lib/{target_triple} ${{CROSS_COMPILER_LIB}}
"""


def _exit_on_error(error, description):
    exit_code = _exit_codes.get(error, _exit_codes['default'])
    print(description)
    sys.exit(exit_code)


Platform = namedtuple('Platform', ['arch', 'os', 'distro', 'rmw'])
Toolchain = namedtuple('Toolchain', ['sysroot_base_image', 'target_arch', 'target_triple'])


_default_platforms = {
    Platform('generic_arm64', 'ubuntu_bionic', 'crystal', 'fastrtps'):
    Toolchain('TODO', 'aarch64', 'aarch64-linux-gnu'),
    # https://packages.debian.org/stretch/gcc-arm-linux-gnueabihf
    # https://cmake.org/cmake/help/v3.6/manual/cmake-toolchains.7.html#cross-compiling-for-linux
    Platform('generic_armhf', 'ubuntu_bionic', 'crystal', 'fastrtps'):
    Toolchain('TODO', 'arm', 'arm-linux-gnueabihf'),
    Platform('generic_armhf', 'ubuntu_bionic', 'dashing', 'fastrtps'):
    Toolchain('TODO', 'arm', 'arm-linux-gnueabihf')
}


def _platform_info_for_args(args):
    platform = Platform(args.arch, args.os, args.distro, args.rmw)
    return _default_platforms[platform]


def _setup_platform_args(parser):
    parser.add_argument(
        '--arch',
        required=True,
        type=str,
        help='Target architecture')
    parser.add_argument(
        '--os',
        required=True,
        type=str,
        help='Target OS')
    parser.add_argument(
        '--distro',
        required=False,
        type=str,
        default='crystal',
        help='Target ROS distribution')
    parser.add_argument(
        '--rmw',
        required=False,
        type=str,
        default='fastrtps',
        help='Target RMW implementation')


def _get_platform_id(args):
    return '{arch}-{os}-{rmw}-{distro}'.format(
        arch=args.arch, os=args.os, rmw=args.rmw, distro=args.distro)


def _get_cc_root(*, wd, context):
    cc_root = wd / _get_platform_id(args=context.args)
    if not cc_root.exists():
        os.makedirs(str(cc_root))
        open(str(cc_root / 'COLCON_IGNORE'), 'w').close()
    return cc_root


class CCBuildVerb(VerbExtensionPoint):
    """Cross-compile ROS 2 packages using an existing sysroot."""

    __test__ = False  # prevent the class to falsely be identified as a test

    def __init__(self):
        super().__init__()
        self._wd = Path.cwd()
        satisfies_version(VerbExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')
        self._build_verb = BuildVerb()

    def add_arguments(self, *, parser):
        _setup_platform_args(parser)
        self._build_verb.add_arguments(parser=parser)

    def _get_additional_cmake_args(self, *, cc_root, args):
        additional_cmake_args = [
            '-DCMAKE_VERBOSE_MAKEFILE:BOOL=ON',
            # TODO: allow using platform specific toolchain file
            '-DCMAKE_TOOLCHAIN_FILE={}'.format(
                (Path(__file__).parent / 'cmake-toolchains' / 'generic_linux.cmake'))
        ]
        return additional_cmake_args

    def main(self, *, context):
        cc_root = _get_cc_root(wd=self._wd, context=context)
        context.args.build_base = str(cc_root / 'build')
        context.args.install_base = str(cc_root / 'install')
        context.args.merge_install = True
        context.args.cmake_force_configure = True
        context.args.cmake_args = (context.args.cmake_args if context.args.cmake_args else [])\
            + self._get_additional_cmake_args(cc_root=cc_root, args=context.args)
        return self._build_verb.main(context=context)


class CCSetupSysrootVerb(VerbExtensionPoint):
    """Setup a sysroot for cross-compiling ROS 2 packages."""

    __test__ = False  # prevent the class to falsely be identified as a test

    def __init__(self):
        super().__init__()
        self._wd = Path.cwd()
        satisfies_version(VerbExtensionPoint.EXTENSION_POINT_VERSION, '^1.0')
        self._docker_client = docker.from_env()
        self._platform_info = None

    def add_arguments(self, *, parser):
        _setup_platform_args(parser)
        parser.add_argument(
            '--sysroot-base-image',
            type=str,
            help='Base image to use for building the sysroot')
        parser.add_argument(
            '--docker-network-mode',
            type=str,
            default='host',
            help="Docker's network_mode parameter to use for all Docker interactions")
        parser.add_argument(
            '--sysroot-nocache',
            type=bool,
            default=False,
            help="When set to true, this disables Docker's cache when building the image for the"
                 'workspace sysroot')
        parser.add_argument(
            '--force-sysroot-build',
            type=bool,
            default=False,
            help='When set to true, we rebuild the sysroot and sysroot image, '
                 'even if any of those is availble')

    def _get_workspace_sysroot_image(self, *, context):
        """
        Ensure a workspace image is available in the local image cache.

        :return: the name of the tag of the workspace image
        """
        # FIXME: add parameter to optionally override this
        workspace_image_tag = self._wd.name + ':latest'
        self._build_workspace_sysroot_image(
            context=context, workspace_image_tag=workspace_image_tag)
        print('Workspace sysroot image {} created with success'.format(workspace_image_tag))
        return workspace_image_tag

    @classmethod
    def _print_docker_logs(cls, logs):
        for stream_obj in logs:
            for line in stream_obj.get('stream', '').split('\n'):
                print('{}'.format(line))

    def _build_workspace_sysroot_image(self, *, context, workspace_image_tag):
        print('Fetching sysroot base image {}'.format(context.args.sysroot_base_image))
        self._docker_client.images.pull(context.args.sysroot_base_image)
        # FIXME consider moving constants to static fields
        workspace_dockerfile_path = (Path(__file__).parent / 'sysroot' / 'Dockerfile_workspace')
        buildargs = {
            'ROS2_BASE_IMG': context.args.sysroot_base_image,
            'ROS2_WORKSPACE': '.',
            'ROS_DISTRO': context.args.distro,
            'TARGET_TRIPLE': self._platform_info['target_triple']
        }
        print('Building workspace image {} (this might take some time...)'.format(
            workspace_image_tag))
        # FIXME: this gives no output until the whole image is built,
        # at least implement some spinner
        try:
            _workspace_image, _ws_image_build_logs = self._docker_client.images.build(
                path='.',
                dockerfile=str(workspace_dockerfile_path),
                tag=workspace_image_tag,
                buildargs=buildargs,
                quiet=False,
                nocache=context.args.sysroot_nocache,
                network_mode=context.args.docker_network_mode)
            print('Docker build output')
            self._print_docker_logs(_ws_image_build_logs)
            print()
        except docker.errors.BuildError as be:
            print('Error building sysroot image')
            print('\tError message: {}'.format(be.msg))
            print('\tBuild log')
            self._print_docker_logs(be.build_log)
            print()
            raise be

    def _setup_workspace_sysroot(self, *, context):
        """
        Set up a sysroot for a workspace.

        :return: a Path object for the directory where the workspace sysroot is located
        """
        cc_root = _get_cc_root(wd=self._wd, context=context)
        target_sysroot_path = cc_root / 'sysroot'
        if not context.args.force_sysroot_build and target_sysroot_path.exists():
            print('Using existing sysroot path [{}]'.format(target_sysroot_path))
        else:
            if not target_sysroot_path.exists():
                print('No sysroot found at path [{}], building it now'.format(target_sysroot_path))
            elif context.args.force_sysroot_build:
                print('Forced sysroot re-build')
            workspace_image_tag = self._get_workspace_sysroot_image(context=context)
            self._export_workspace_sysroot_image(
                context=context,
                workspace_image_tag=workspace_image_tag,
                target_sysroot_path=target_sysroot_path)
        return target_sysroot_path

    def _export_workspace_sysroot_image(
        self, *, context, workspace_image_tag, target_sysroot_path
    ):
        print('Exporting sysroot to path [{}]'.format(target_sysroot_path))
        shutil.rmtree(str(target_sysroot_path), ignore_errors=True)
        tmp_sysroot_dir = tempfile.mkdtemp(suffix='-cc_build')
        sysroot_tarball_path = Path(tmp_sysroot_dir) / 'sysroot.tar'
        print('Exporting filesystem of image {} into tarball {}'.format(
            workspace_image_tag, sysroot_tarball_path))
        try:
            sysroot_container = self._docker_client.containers.run(
                image=workspace_image_tag, detach=True)
            with open(str(sysroot_tarball_path), 'wb') as out_f:
                out_f.writelines(sysroot_container.export())
            sysroot_container.stop()
            with tarfile.open(str(sysroot_tarball_path)) as sysroot_tar:
                relevant_dirs = ['lib', 'usr', 'etc', 'opt', 'root_path']
                relevant_members = (
                    m for m in sysroot_tar.getmembers()
                    if re.match('^({}).*'.format('|'.join(relevant_dirs)), m.name) is not None)
                sysroot_tar.extractall(str(target_sysroot_path), members=relevant_members)
        finally:
            shutil.rmtree(tmp_sysroot_dir, ignore_errors=True)
        print('Success exporting sysroot to path [{}]'.format(target_sysroot_path))

    def _setup_argument_defaults(self, *, args):
        """Set up argument defaults, that depend on the values for other arguments."""
        # TODO: check args against _default_platforms
        if not args.sysroot_base_image:
            # TODO: determine args.sysroot_base_image from _default_platforms and args
            raise NotImplementedError

    def _write_cc_build_setup_file(self, *, cc_root, context):
        cc_build_setup_file_path = cc_root / 'cc_build_setup.bash'
        cc_build_setup_file_contents = _CC_BUILD_SETUP_FILE_TEMPLATE.format(
            ros_root='{cc_root}/sysroot/opt/ros/{distro}'.format(
              cc_root=cc_root, distro=context.args.distro),
            target_arch=self._platform_info['target_arch'],
            target_triple=self._platform_info['target_triple'],
            cc_root=cc_root)
        with open(str(cc_build_setup_file_path), 'w') as out_f:
            out_f.write(cc_build_setup_file_contents)
        return cc_build_setup_file_path

    def _write_cc_system_setup_script(self, *, cc_root):
        cc_system_setup_script_path = cc_root / 'cc_system_setup.bash'
        cc_system_setup_script_contents = _CC_BUILD_SYSTEM_SETUP_SCRIPT_TEMPLATE.format(
            cc_root=cc_root,
            target_triple=self._platform_info['target_triple']
        )
        with open(str(cc_system_setup_script_path), 'w') as out_f:
            out_f.write(cc_system_setup_script_contents)
        return cc_system_setup_script_path

    def main(self, *, context):
        self._setup_argument_defaults(args=context.args)
        self._platform_info = _platform_info_for_args(context.args)
        self._setup_workspace_sysroot(context=context)
        cc_root = _get_cc_root(wd=self._wd, context=context)
        cc_build_setup_file_path = self._write_cc_build_setup_file(
          cc_root=cc_root, context=context)
        # generalization of the Poco hack
        # from https://github.com/ros2/cross_compile/blob/master/entry_point.sh#L38
        cc_system_setup_script_path = self._write_cc_system_setup_script(cc_root=cc_root)
        print("""

In order to complete the cross compilation setup, please

1. WORKAROUND: run the command below to setup using sysroot's GLIBC for cross compilation.

    bash {cc_system_setup_script_path}

2. Run

    source {cc_build_setup_file_path}

to setup the cross compilation build environment

""".format(cc_system_setup_script_path=cc_system_setup_script_path,
           cc_build_setup_file_path=cc_build_setup_file_path))
        return 0
