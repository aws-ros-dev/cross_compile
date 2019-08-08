# Colcon cc-build

Colcon plugin for cross-compilation

## Install prerequisites

### Ubuntu

The cross compilation toolchain and docker have to be installed. 
The following instruction have been tested on Ubuntu Xenial (16.04) and Bionic (18.04).

```bash
# Install cross compilation toolchain
sudo apt-get update
sudo apt-get install -y build-essential cmake git wget curl lsb-core bash-completion qemu-user-static g++-aarch64-linux-gnu g++-arm-linux-gnueabihf python3-pip htop
sudo python3 -m pip install -U  colcon-common-extensions rosdep vcstool

# Also install docker and make it available to the current user: https://docs.docker.com/install/linux/docker-ce/ubuntu/
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg-agent software-properties-common
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
sudo apt-key fingerprint 0EBFCD88
sudo add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable"
sudo apt-get update
sudo apt-get install -y docker-ce
sudo usermod -aG docker $USER
newgrp docker # this reloads the group permissions in the current shell, unnecessary after relogin
docker run hello-world
```

### Mac
The following instruction have been tested on Mac OS Mojave (10.14).

```bash
# Ensure your brew install is healthy
brew doctor
brew install cmake git wget curl bash-completion qemu
python3 -m pip install --user -U  colcon-common-extensions rosdep vcstool
```

[Install Docker toolbox](https://docs.docker.com/toolbox/toolbox_install_mac/)

## Usage

We need to setup our sysroot directory for the docker image. Docker can only copy from a specific
context so all these assets need to be copied relative to the `Dockerfile` path.
```bash
# Create a directory to store qemu assets
mkdir qemu-user-static
cp /usr/bin/qemu-* $ROS2_WORKSPACE/qemu-user-static 
# Copy ROS Sources
cp -r ~/ros2_ws/src .
```

In the end your Directory should be as follows:
```bash
sysroot
 |
 |-- Dockerfile_workspace
 +-- qemu-user-static
 |   +-- qemu-*-static
 +-- src
     + -- ros2 packages ...
```

### Building a workspace

```bash
# Install plugin
pip3 install --editable src/Colcon-cc-build/colcon_cc_build --user

# Launch a build
## 1. setup the sysroot
## add --force-sysroot-build to force rebuilding the sysroot
colcon cc-setup-sysroot --arch generic_armhf --os ubuntu_bionic \
   --sysroot-base-image <Dockerfile-base-image>
## 2. Complete cc-build setup following the instructions in the output of the previous command
bash generic_armhf-ubuntu_bionic-fastrtps-crystal/cc_system_setup.bash
source generic_armhf-ubuntu_bionic-fastrtps-crystal/cc_build_setup.bash
## 3. launch cross compilation using that sysroot
colcon cc-build --arch generic_armhf --os ubuntu_bionic \
  --packages-up-to examples_rclcpp_minimal_publisher
```
where `Dockerfile-base-image` is a Dockerfile hosted somewhere or built on your computer.

#### Sample Docker images

Prefix all image links with `035662560449.dkr.ecr.us-east-2.amazonaws.com/`.

| Architecture | OS                  | Distro             | DDS      | Image Link                                       |
|--------------|---------------------|--------------------|----------|--------------------------------------------------|
| armhf        | Ubuntu Bionic 18.04 | Crystal            | FastRTPS | TODO                                             |
| aarch64      | Ubuntu Bionic 18.04 | Dashing (Prebuilt) | FastRTPS | cc-tool:aarch64-bionic-dashing-fastrtps-prebuilt |
| aarch64      | Ubuntu Bionic 18.04 | Dashing            | FastRTPS | TODO                                             |

#### Assumptions

- The Docker image for `--sysroot-base-image` installs the ROS 2 distro at `/opt/ros/${distro}`.

Additional base images can be created using the script `colcon_cc_build/colcon_cc_build/verb/sysroot/publish_to_ECR.sh`.

### Troubleshooting

#### Debug

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


#### Lib Poco Issue
From the ROS2 Cross compilation docs:
> The Poco pre-built has a known issue where it is searching for libz and libpcre on the host system instead of SYSROOT. 
> As a workaround for the moment, please link both libraries into the the host’s file-system.
> ```bash
> mkdir -p /usr/lib/$TARGET_TRIPLE
> ln -s `pwd`/sysroot_docker/lib/$TARGET_TRIPLE/libz.so.1 /usr/lib/$TARGET_TRIPLE/libz.so
> ln -s `pwd`/sysroot_docker/lib/$TARGET_TRIPLE/libpcre.so.3 /usr/lib/$TARGET_TRIPLE/libpcre.so
> ```