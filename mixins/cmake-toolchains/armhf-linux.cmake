set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_VERSION 1)
set(CMAKE_SYSTEM_PROCESSOR $ENV{TARGET_ARCH})

# Specify the cross compiler
set(CMAKE_C_COMPILER /usr/bin/$ENV{TARGET_TRIPLE}-gcc)
set(CMAKE_CXX_COMPILER /usr/bin/$ENV{TARGET_TRIPLE}-g++)

# Specify the target file system
set(CMAKE_SYSROOT $ENV{CC_ROOT}/sysroot)
set(CMAKE_FIND_ROOT_PATH
    "$ENV{CC_ROOT}/sysroot/root_path"
    "$ENV{CC_ROOT}/install")

SET(CMAKE_INSTALL_RPATH "$ENV{CC_ROOT}/sysroot/opt/ros/$ENV{ROS_DISTRO}/lib")
SET(CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)

# Specify the python SOABI
set(PYTHON_SOABI cpython-36m-$ENV{TARGET_TRIPLE})

if(NOT TARGET Qt5::moc)
  set(QT_MOC_EXECUTABLE /usr/bin/moc)
  add_executable(Qt5::moc IMPORTED)
  set_property(TARGET Qt5::moc PROPERTY IMPORTED_LOCATION ${QT_MOC_EXECUTABLE})
endif()

# This assumes that pthread will be available on the target system
# (this emulates that the return of the TRY_RUN is a return code "0")
set(THREADS_PTHREAD_ARG "0"
  CACHE STRING "Result from TRY_RUN" FORCE)
