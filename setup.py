#!/usr/bin/env python

from setuptools import setup, find_packages
import sys

setup(name='pydelfi',
      version='v0.2',
      description='LFI in TensorFlow',
      author='Justin Alsing, Tom Charnock, Stephen Feeney',
      url='https://github.com/justinalsing/pydelfi',
      packages=find_packages(),
      install_requires=[
          #"tf-nightly==2.2.0-dev20200412",
          "tensorflow>=v2.1.0",
          "tensorflow-probability>=0.9.0",
          "getdist>=1.1.0",
          "emcee>=3.0.2",
          "scipy>=1.4.1",
          "tqdm>=4.41.1",
          "numpy>=1.18.1"
      ])
