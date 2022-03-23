from pathlib import Path

from setuptools import find_packages, setup

ROOT = Path(__file__).parent


setup(
    name='django-subscriptions-rt',
    version='0.1',
    author='Aleksandr Goncharov',
    author_email='aleksandr.goncharov@reef.pl',
    url='https://github.com/reef-technologies/django-subscriptions-rt',
    download_url="http://pypi.python.org/pypi/django-subscriptions-rt/",
    description="Subscriptions and payments for your django app",
    long_description=(ROOT / 'README.md').read_text(),
    # license='',
    install_requires=(ROOT / 'requirements.txt').read_text().split('\n'),
    # extras_require={
    #     'docs': ['sphinx', 'sphinx-autobuild'],
    # },
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    classifiers=[
        'Operating System :: OS Independent',
        'Intended Audience :: Developers',
        'Environment :: Web Environment',
        'Framework :: Django',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
)
