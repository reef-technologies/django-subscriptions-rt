import nox


DEMO_APP_DIR = 'demo'
PYTHON_VERSIONS = ['3.7', '3.8', '3.9', '3.10']
DJANGO_VERSIONS = ['3.0.14', '3.1.14', '3.2.12', '4.0.2']

nox.options.default_venv_backend = 'venv'
nox.options.stop_on_first_error = True
nox.options.reuse_existing_virtualenvs = True


@nox.session(python=PYTHON_VERSIONS)
def lint(session):
    session.install('flake8', 'mypy')
    session.run('flake8', DEMO_APP_DIR)
    session.run('mypy', DEMO_APP_DIR)


@nox.session(python=PYTHON_VERSIONS)
@nox.parametrize('django', DJANGO_VERSIONS)
def test(session, django: str):
    session.install('pytest', f'django=={django}')
    session.run('pytest', DEMO_APP_DIR)
