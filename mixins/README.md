Check your mixin linting by running:
```bash
python3 lint.py
```
Your mixin arguments need to be sorted alphabetically, otherwise you
will get linting errors.

Install these mixins using
```bash
colcon mixin add cc_mixins file://<path_to_mixin_directory>/index.yaml
colcon mixin update cc_mixins
```
where `cc_mixins` is the name of your

Check the mixins are installed by running
```bash
colcon mixin show
```