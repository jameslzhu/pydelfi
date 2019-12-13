#!/usr/bin/env python

from setuptools import setup, find_packages
import sys

setup(name='pydelfi',
      version='v0.1',
      description='LFI in TensorFlow',
      author='Justin Alsing',
      url='https://github.com/justinalsing/pydelfi',
      packages=find_packages(),
      install_requires=[
          "tensorflow~=v1.1.0",
          "getdist~=1.0.0",
          "emcee~=3.0.0",
          "mpi4py~=3.0.3",
          "scipy~=1.3.3",
          "tqdm~=4.40.2",
      ])
