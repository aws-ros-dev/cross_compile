#!/usr/bin/env python

import argparse
import logging
from string import Template

from cross_compile.sysroot_compiler import DockerConfig
from cross_compile.sysroot_compiler import Platform
from cross_compile.sysroot_compiler import SysrootCompiler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


CC_COMPLETE_STRING = Template('To setup the cross compilation build environment: 1. Run the command below to setup '
                              'using sysroot\'s GLIBC for cross-compilation.\n`bash $system_setup_script_path`\n2. Run '
                              'the command below to export the environment variables used by the cross-compiled ROS '
                              'packages.\n`source $build_setup_file_path`')


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

    # Main pipeline
    sysroot_create = SysrootCompiler(cc_root_dir=args.sysroot_path, ros_workspace_dir=args.sros2_workspace,
                                     platform=platform, docker_config=docker_args)
    sysroot_create.execute_cc_pipeline()

    logger.info(CC_COMPLETE_STRING.substitute(
        system_setup_script_path=sysroot_create.get_system_setup_script_path(),
        build_setup_file_path=sysroot_create.get_build_setup_script_path()))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logging.exception(e)
        exit(1)