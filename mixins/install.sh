#!/usr/bin/env bash

python3 lint.py
if [ $? -eq 0]; then
  colcon mixin add cc_mixins file://`pwd`/index.yaml
  colcon mixin update cc_mixins
else
  echo "Linting failed. Check your linting and run again to install mixins."
fi