#! /usr/bin/env python3

from setuptools import setup

with open("README.rst") as readme:
    long_descr = readme.read()

setup(
    name="httpuploader",
    version="0.1.1",
    py_modules=["httpuploader"],
    entry_points = {
        'console_scripts': ['httpuploader=httpuploader:main'],
    },
    author="Javier Llopis",
    author_email="javier@llopis.me",
    url="https://github.com/destrangis/httpuploader",
    description="A directory listing server that accepts file uploads.",
    long_description_content_type="text/x-rst",
    long_description=long_descr,
    classifiers=[
    "Programming Language :: Python",
    "Programming Language :: Python :: 3 :: Only",
    "License :: OSI Approved :: MIT License",
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Environment :: Web Environment",
    "Intended Audience :: End Users/Desktop",
    "Intended Audience :: Developers",
    "Intended Audience :: System Administrators",
    "Operating System :: OS Independent",
    "Topic :: Internet :: WWW/HTTP",
    "Topic :: Internet :: WWW/HTTP :: WSGI",
    "Topic :: Internet :: WWW/HTTP :: WSGI :: Application",
    ]
)
