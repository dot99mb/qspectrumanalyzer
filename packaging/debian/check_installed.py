#!/usr/bin/python3 -I
"""Run in the Debian test container after installing the DEB."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

root = '/usr/lib/qspectrumanalyzer'
sys.path[:0] = [root, root + '/vendor']
os.environ['QT_PREFERRED_BINDING'] = 'PyQt5'
os.environ['PYQTGRAPH_QT_LIB'] = 'PyQt5'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PATH'] = root + '/bin:' + os.environ['PATH']
import qspectrumanalyzer
assert Path(qspectrumanalyzer.__file__).is_relative_to(root)
import pyqtgraph
import numpy
import SoapySDR
import soapypower.writer

assert 'numpy.fromstring(' not in Path(soapypower.writer.__file__).read_text()
print('Installed package:', qspectrumanalyzer.__file__, flush=True)
print('Python:', sys.version.split()[0], 'PyQtGraph:', pyqtgraph.__version__, 'NumPy:', numpy.__version__, flush=True)
subprocess.run(['/usr/bin/qspectrumanalyzer', '--version'], check=True)
subprocess.run([root + '/bin/soapy_power', '--help'], check=True, stdout=subprocess.DEVNULL)
suite = unittest.defaultTestLoader.discover('/src/tests')
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(not result.wasSuccessful())
