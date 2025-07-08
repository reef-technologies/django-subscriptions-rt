# cookiecutter-rt-pkg macro: requires cookiecutter.is_django_package
import pytest


@pytest.mark.django_db(databases=["actual_db"])
def test__setup():
    pass
