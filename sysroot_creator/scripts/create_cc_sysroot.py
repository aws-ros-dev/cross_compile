# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Script to generate sysroot for cross-compiling ROS2."""

import argparse
import logging
import os
from pathlib import Path
import re
import shutil
from string import Template
import subprocess
import tarfile
import tempfile
from typing import Dict

import docker

import jinja2


CC_BUILD_SETUP_FILE_TEMPLATE = jinja2.Template("""
shell=`echo $SHELL | awk -F/ '{print $NF}'`
if [ -d {{ros_root}} ]
then
    source {{ros_root}}/setup.$shell
else
    echo "WARNING: no ROS distro found on the sysroot"
fi

export TARGET_ARCH={{target_arch}}
export TARGET_TRIPLE={{target_triple}}
export CC_ROOT={{cc_root}}
""")  # noqa

CC_BUILD_SYSTEM_SETUP_SCRIPT_TEMPLATE = jinja2.Template("""
sudo rm -rf /lib/{{target_triple}}
sudo ln -s {{cc_root}}/sysroot/lib/{{target_triple}} /lib/{{target_triple}}
sudo rm -rf /usr/lib/{{target_triple}}
sudo ln -s {{cc_root}}/sysroot/usr/lib/{{target_triple}} /usr/lib/{{target_triple}}

CROSS_COMPILER_LIB=/usr/{{target_triple}}/lib
CROSS_COMPILER_LIB_BAK=/usr/{{target_triple}}/lib_$(date +%s).bak
echo "Backing up $CROSS_COMPILER_LIB to $CROSS_COMPILER_LIB_BAK"
sudo mv $CROSS_COMPILER_LIB $CROSS_COMPILER_LIB_BAK
sudo ln -s {{cc_root}}/sysroot/lib/{{target_triple}} $CROSS_COMPILER_LIB
""")  # noqa

ROS_WS_DIR_ERROR_STRING = Template('\'$ros_ws\' does not exist in the sysroot directory. Make '
                                   'sure you copy your packages as \'$ros_ws/src\' into the '
                                   '\'sysroot\' directory.')
QEMU_DIR_ERROR_STRING = Template('\'$qemu_dir\' does not exist in the sysroot directory. Make '
                                 'sure you copy the binaries from \'/usr/bin/qemu-*\' into the '
                                 'sysroot directory.')
QEMU_EMPTY_ERROR_STRING = Template('\'$qemu_dir\' is empty. Make sure you copy the binaries from '
                                   '\'/usr/bin/qemu-*\' into \'$qemu_dir\'.')
COPY_DOCKER_WS_ERROR_STRING = Template('Unable to copy the \'$dockerfile\' file. Make sure you '
                                       'have write permissions to the sysroot directory.')
SYSROOT_NOT_FOUND_ERROR_STRING = Template('Sysroot directory not found at \'$sysroot_dir\'. Make '
                                          'sure you specify the full path to the directory '
                                          'containing \'sysroot\'.')
SYSROOT_DIR_NAME: str = 'sysroot'
QEMU_DIR_NAME: str = 'qemu-user-static'
DOCKER_WS_NAME: str = 'Dockerfile_workspace'
DOCKER_CLIENT = docker.from_env()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Platform:
    """A class that represents platform specification for cross compiling.

    Includes:
    1. Target architecture
    2. Target operating system
    3. ROS2 distribution used
    4. RMW implementation used
    """

    def __init__(self, args):
        """Initialize platform parameters."""
        self.arch = args.arch
        self.os = args.os
        self.distro = args.distro
        self.rmw = args.rmw

        if self.arch == 'armhf':
            self.cc_toolchain = 'arm-linux-gnueabihf'
        elif self.arch == 'aarch64':
            self.cc_toolchain = 'aarch64-linux-gnu'

    def __str__(self):
        """Return string representation of platform parameters."""
        return '-'.join((self.arch, self.os, self.rmw, self.distro))

    def get_workspace_image_tag(self) -> str:
        """Generate docker image name and tag."""
        return os.getenv('USER') + '/' + str(self) + ':latest'


class DockerConfig:
    """
    A class that represents docker build parameters used in creating sysroot.

    Includes:
    1. Base docker image to use for building sysroot
    2. Docker network mode
    3. Setting to enable/disable caching during docker build
    """

    _default_docker_base_image: Dict[tuple, str] = {
        ('armhf', 'ubuntu'): 'arm32v7/ubuntu:bionic',
        ('armhf', 'debian'): 'arm32v7/debian:latest',
        ('aarch64', 'ubuntu'): 'arm64v8/ubuntu:bionic',
        ('aarch64', 'debian'): 'arm64v8/debian:latest',
    }

    def __init__(self, args):
        """Initialize docker configuration."""
        if args.sysroot_base_image is None:
            self.base_image = \
                self._default_docker_base_image[args.arch, args.os]
        else:
            self.base_image = args.sysroot_base_image

        self.network_mode = args.docker_network_mode
        self.nocache = args.sysroot_nocache

    def __str__(self):
        """Return string representation of docker build parameters."""
        return 'Base Image: {}\nNetwork Mode: {}\nCaching: {}'.format(
            self.base_image, self.network_mode, self.nocache)


class SysrootCreator:
    """A class that configures and builds a Docker container for cross-compiling ROS2 packages."""
    def __init__(
      self,
      cc_root_dir: str,
      ros_workspace_dir: str,
      platform: Platform,
      docker_config: DockerConfig) -> None:
        """
        Construct a SysrootCreator object the builds a Docker container used for cross compilation.

        :param cc_root_dir: The directory containing the 'sysroot' directory with the ROS2
        workspace and QEMU binaries.
        :param ros_workspace_dir: The name of the directory containing the ROS2 packages (inside a
        'src' directory).
        :param platform: A custom object used to specify the the platform for cross-compilation.
        :param docker_config: A custom object used to specify the configuration of the Docker
        image to build.
        """
        if not isinstance(cc_root_dir, str):
            raise TypeError("Argument `cc_root_dir` must be of type string.")
        if not isinstance(ros_workspace_dir, str):
            raise TypeError("Argument `ros_workspace_dir` must be of type string.")
        if not isinstance(platform, Platform):
            raise TypeError("Argument `platform` must be of type Platform.")
        if not isinstance(docker_config, DockerConfig):
            raise TypeError("Argument `docker_config` must be of type DockerConfig.")

        self._cc_root_dir = Path(cc_root_dir)
        self._ros_workspace_dir = Path(ros_workspace_dir)
        self._target_sysroot = self._cc_root_dir / SYSROOT_DIR_NAME
        self._ros_ws_directory = self._target_sysroot / self._ros_workspace_dir
        self._qemu_directory = self._target_sysroot / QEMU_DIR_NAME
        self._dockerfile_directory = Path(__file__).parent / DOCKER_WS_NAME
        self._expected_dockerfile_directory = self._target_sysroot / DOCKER_WS_NAME
        self._system_setup_script_path = Path()
        self._build_setup_script_path = Path()
        self._platform = platform
        self._docker_config = docker_config

        try:
            self._setup_sysroot_dir()
        except FileNotFoundError as e:
            logger.exception(e)

    def get_system_setup_script_path(self) -> Path:
        """Returns the path to the system setup script."""
        return self._system_setup_script_path

    def get_build_setup_script_path(self) -> Path:
        """Returns the path to the build setup script."""
        return self._build_setup_script_path

    def _setup_sysroot_dir(self) -> None:
        """Checks to make sure the sysroot directory is setup correctly.

        Raises FileNotFoundError's if any of the components necessary for cross compilation is
        missing. Copies the Dockerfile_workspace to the 'sysroot' directory in order to copy the
        assets to it (see https://docs.docker.com/engine/reference/builder/#copy).
        """
        logger.info('Checking sysroot directory...')
        if self._target_sysroot.exists():
            logger.debug('Sysroot directory exists.')
            if not self._ros_ws_directory.exists():
                raise FileNotFoundError(ROS_WS_DIR_ERROR_STRING.substitute(
                    ros_ws=self._ros_workspace_dir))
            logger.debug('ROS workspace exists.')
            if not self._qemu_directory.exists():
                raise FileNotFoundError(QEMU_DIR_ERROR_STRING.substitute(qemu_dir=QEMU_DIR_NAME))
            if not os.listdir(str(self._qemu_directory.absolute())):
                raise FileNotFoundError(QEMU_EMPTY_ERROR_STRING.substitute(qemu_dir=QEMU_DIR_NAME))
            logger.debug('QEMU binaries exist')
            shutil.copy(str(self._dockerfile_directory), str(self._target_sysroot))
            if not self._expected_dockerfile_directory.exists():
                raise FileNotFoundError(COPY_DOCKER_WS_ERROR_STRING.substitute(
                    dockerfile=DOCKER_WS_NAME))
        else:
            raise FileNotFoundError(SYSROOT_NOT_FOUND_ERROR_STRING.substitute(
                sysroot_dir=self._cc_root_dir))

    def build_workspace_sysroot_image(self) -> None:
        """Build the target sysroot docker image."""
        logger.info('Fetching sysroot base image: {}'.format(self._docker_config.base_image))
        DOCKER_CLIENT.images.pull(self._docker_config.base_image)
        image_tag = self._platform.get_workspace_image_tag()
        buildargs = {
            'ROS2_BASE_IMG': self._docker_config.base_image,
            'ROS2_WORKSPACE': str(self._ros_workspace_dir),
            'ROS_DISTRO': self._platform.distro,
            'TARGET_TRIPLE': self._platform.cc_toolchain,
            'TARGET_ARCH': self._platform.arch,
        }
        logger.debug("Build Arguments: {}")
        logger.info('Building workspace image: {}'.format(image_tag))

        # Switch to low-level API to expose build logs
        docker_client = docker.APIClient(base_url='unix://var/run/docker.sock')
        # Note the difference:
        # path – Path to the directory containing the Dockerfile
        # dockerfile – Path within the build context to the Dockerfile
        log_generator = docker_client.build(
            path=str(self._target_sysroot),
            dockerfile=str(self._expected_dockerfile_directory),
            tag=image_tag,
            buildargs=buildargs,
            quiet=False,
            nocache=self._docker_config.nocache,
            network_mode=self._docker_config.network_mode,
            decode=True)
        for chunk in log_generator:
            # There are usually two outputs we want to capture, stream and error.
            # We also want to remove newline (\n) and carriage returns (\r) to
            # avoid mangled output.
            error_line = chunk.get('error', None)
            if error_line:
                logger.exception('Error building sysroot image. The following error'
                                 ' was caught:\n{}'.format(error_line))
                raise docker.errors.BuildError(reason=error_line, build_log=error_line)
            line = chunk.get('stream', '')
            line = line.rstrip().lstrip()
            if line:
                logger.info(line)

        logger.info('Successfully created sysroot docker image: {}'.format(image_tag))

    def export_workspace_sysroot_image(self) -> None:
        """Export sysroot filesystem into sysroot directory."""
        logger.info('Exporting sysroot to path [{}]'.format(self._target_sysroot))
        shutil.rmtree(str(self._target_sysroot), ignore_errors=True)
        # TODO: Use context to make sure temp directory doesn't leak
        tmp_sysroot_dir = tempfile.mkdtemp(suffix='-cc_build')
        sysroot_tarball_path = Path(tmp_sysroot_dir) / (SYSROOT_DIR_NAME + '.tar')
        image_tag = self._platform.get_workspace_image_tag()
        logger.info('Exporting filesystem of image {} into tarball {}'.format(
            image_tag, sysroot_tarball_path))

        try:
            sysroot_container = DOCKER_CLIENT.containers.run(image=image_tag, detach=True)
            with open(str(sysroot_tarball_path), 'wb') as out_f:
                out_f.writelines(sysroot_container.export())
            sysroot_container.stop()
            with tarfile.open(str(sysroot_tarball_path)) as sysroot_tar:
                relevant_dirs = ['lib', 'usr', 'etc', 'opt', 'root_path', 'ros2_ws/install']
                relevant_members = (
                    m for m in sysroot_tar.getmembers()
                    if re.match('^({}).*'.format('|'.join(relevant_dirs)), m.name) is not None
                )
                sysroot_tar.extractall(str(self._target_sysroot), members=relevant_members)
        finally:
            shutil.rmtree(tmp_sysroot_dir, ignore_errors=True)

        logger.info('Success exporting sysroot to path [{}]'.format(self._target_sysroot))

    def _write_cc_build_setup_script(self) -> Path:
        """Create setup file for cross-compile build."""
        cc_build_setup_file_path = self._target_sysroot / 'cc_build_setup.bash'
        cc_build_setup_file_contents = CC_BUILD_SETUP_FILE_TEMPLATE.render(
            target_arch=self._platform.arch,
            target_triple=self._platform.cc_toolchain,
            cc_root=self._target_sysroot,
            ros_root='{cc_root_dir}/sysroot/opt/ros/{distro}'.format(
                cc_root_dir=self._target_sysroot, distro=self._platform.distro))
        with open(str(cc_build_setup_file_path), 'w') as out_f:
            out_f.write(cc_build_setup_file_contents)
        return cc_build_setup_file_path

    def _write_cc_system_setup_script(self) -> Path:
        """Create setup file for sysroot setup."""
        cc_system_setup_script_path = self._target_sysroot / 'cc_system_setup.bash'
        cc_system_setup_script_contents = CC_BUILD_SYSTEM_SETUP_SCRIPT_TEMPLATE.render(
            cc_root=self._target_sysroot,
            target_triple=self._platform.cc_toolchain)
        with open(str(cc_system_setup_script_path), 'w') as out_f:
            out_f.write(cc_system_setup_script_contents)
        return cc_system_setup_script_path

    def write_setup_scripts(self) -> None:
        """Write both the build and system setup scripts."""
        self._system_setup_script_path = self._write_cc_build_setup_script()
        self._build_setup_script_path = self._write_cc_system_setup_script()

    def setup_sysroot_environment(self) -> None:
        """Set up the environment with variables and symbolic links."""
        logger.info('Sourcing sysroot environment...')
        logger.info("Executing 'bash {}'".format(self._system_setup_script_path))
        subprocess.run(['bash', str(self._system_setup_script_path)])
        logger.info("Executing 'source {}'".format(self._build_setup_script_path))
        subprocess.run(['source', str(self._build_setup_script_path)], shell=True)


def create_arg_parser():
    """Parse command line arguments."""
    example_text = '''Example usage:
    
    python3 create_cc_sysroot.py --arch armhf --os debian
    python3 create_cc_sysroot.py --arch aarch64 --os ubuntu
    python3 create_cc_sysroot.py -a armhf -o ubuntu -d dashing -r fastrtps \
--sysroot-base-image arm64v8/ubuntu:bionic
    '''
    parser = argparse.ArgumentParser(
        description='Sysroot creator for cross compilation workflows.',
        epilog=example_text,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '-a', '--arch',
        required=True,
        type=str,
        choices=['armhf', 'aarch64'],
        help='Target architecture')
    parser.add_argument(
        '-o', '--os',
        required=True,
        type=str,
        choices=['ubuntu', 'debian'],
        help='Target OS')
    parser.add_argument(
        '-d', '--distro',
        required=False,
        type=str,
        default='dashing',
        choices=['ardent', 'bouncy', 'crystal', 'dashing'],
        help='Target ROS distribution')
    parser.add_argument(
        '-r', '--rmw',
        required=False,
        type=str,
        default='fastrtps',
        choices=['fastrtps', 'opensplice', 'connext'],
        help='Target RMW implementation')
    parser.add_argument(
        '--sysroot-base-image',
        required=False,
        type=str,
        help='Base Docker image to use for building the sysroot. '
             'Ex. arm64v8/ubuntu:bionic')
    parser.add_argument(
        '--docker-network-mode',
        required=False,
        type=str,
        default='host',
        help="Docker's network_mode parameter to use for all Docker "
             'interactions')
    parser.add_argument(
        '--sysroot-nocache',
        action='store_true',
        required=False,
        help="When set to true, this disables Docker's cache when building "
             'the image for the workspace sysroot')
    parser.add_argument(
        '--ros2-workspace',
        required=False,
        type=str,
        default='ros2_ws',
        help="The location of the ROS2 workspace you'll be cross compiling "
             'against. Usually ros2_ws if you moved it correctly.')
    parser.add_argument(
        '--sysroot-path',
        required=False,
        default=None,
        type=str,
        nargs='?',
        help="The full path to the directory containing 'sysroot'. The 'ros2_ws/src' and "
             "'qemu-user-static' directories and the 'Dockerfile_workspace' file used to "
             "cross-compile the ROS packages should all be in this directory. Defaults to the "
             "current working directory.")
    return parser


def main():
    """Start the cross-compilation workflow."""
    # Configuration
    parser = create_arg_parser()
    args = parser.parse_args()
    platform = Platform(args)
    docker_args = DockerConfig(args)
    sysroot_path = args.sysroot_path
    ros2_workspace = args.ros2_workspace

    # Main pipeline
    sysroot_create = SysrootCreator(cc_root_dir=sysroot_path, ros_workspace_dir=ros2_workspace,
                                    platform=platform, docker_config=docker_args)
    sysroot_create.build_workspace_sysroot_image()
    sysroot_create.export_workspace_sysroot_image()
    sysroot_create.write_setup_scripts()
    sysroot_create.setup_sysroot_environment()

    logger.info("""
    To setup the cross compilation build environment:

    1. Run the command below to setup using sysroot's GLIBC for \
cross-compilation.
       bash {cc_system_setup_script_path}

    2. Run the command below to export the environment variables used by the \
cross-compiled ROS packages.
       source {cc_build_setup_file_path}

    """.format(cc_system_setup_script_path=sysroot_create.get_system_setup_script_path(),
               cc_build_setup_file_path=sysroot_create.get_build_setup_script_path()))


if __name__ == '__main__':
    main()
