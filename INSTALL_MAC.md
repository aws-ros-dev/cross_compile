# Installation instructions for Mac

## Usage

### Install prerequisites

The cross compilation toolchain and docker have to be installed. The following instruction have been tested on Ubuntu Xenial.

```bash
# Make sure you're ready to install stuff
brew doctor
brew install cmake git wget curl bash-completion qemu
sudo python3 -m pip install -U  colcon-common-extensions rosdep vcstool
```

Install Docker toolbox [here](https://docs.docker.com/toolbox/toolbox_install_mac/)

### Building a workspace

```bash
# Install plugin
pip3 install --editable src/Colcon-cc-build/colcon_cc_build --user

# Launch a build
## 1. setup the sysroot
## add --force-sysroot-build to force rebuilding the sysroot
colcon cc-setup-sysroot --arch generic_armhf --os ubuntu_bionic \
   --sysroot-base-image juanrh/ros2:armhf_bionic_crystal_fastrtps_prebuilt-crystal
## 2. Complete cc-build setup following the instructions in the output of the previous command
bash generic_armhf-ubuntu_bionic-fastrtps-crystal/cc_system_setup.bash
source generic_armhf-ubuntu_bionic-fastrtps-crystal/cc_build_setup.bash
## 3. launch cross compilation using that sysroot
colcon cc-build --arch generic_armhf --os ubuntu_bionic \
  --packages-up-to examples_rclcpp_minimal_publisher
```

### Assumptions

- The Docker image for `--sysroot-base-image` installs the ROS 2 distro at `/opt/ros/${distro}`.

Additional base images can be created using the script `colcon_cc_build/colcon_cc_build/verb/sysroot/publish_to_ECR.sh`.
Some base images already available to use with `--sysroot-base-image`:

- `juanrh/ros2:armhf_bionic_crystal_fastrtps_prebuilt-crystal` for building ROS 2 packages starting from a prebuilt binary for ROS 2 crystal for armhf.
- `juanrh/ros2:armhf_bionic_crystal_base-crystal` for building ROS 2 from scratch.

### Debug

Manually build and/or run the workspace image

```bash
docker image build -f colcon_cc_build/colcon_cc_build/verb/sysroot/Dockerfile_workspace \
  --network host \
  -t ros2_benchmark_pipeline:latest \
  --build-arg ROS2_BASE_IMG=913674827342.dkr.ecr.us-west-2.amazonaws.com/ros2:ubuntu_arm-crystal \
  --build-arg ROS2_WORKSPACE=. --build-arg ROS_DISTRO=crystal --build-arg TARGET_TRIPLE=aarch64-linux-gnu \
  .

docker container run -it --rm --network=host --name test ros2_benchmark_pipeline:latest bash
```
