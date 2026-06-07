from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in erpnext_banking/__init__.py
from erpnext_banking import __version__ as version

setup(
	name="erpnext_banking",
	version=version,
	description="Bank integration for ERPNext (Fio + extensible to other providers)",
	author="PropertyPixel-Studio",
	author_email="krysta@propix.cz",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
