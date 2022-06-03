from setuptools import setup
try:
    from pypandoc import convert_file
    read_md = lambda f: convert_file(f, 'rst')
except ImportError:
    print("warning: pypandoc module not found, could not convert Markdown to RST")
    read_md = lambda f: open(f, 'r').read()

setup(
  name = 'PyNCBI',
  packages = ['PyNCBI'],
  version = '0.1.6.1',
  license='MIT',
  description = 'Simple API for Python Integration with NCBI',
  long_description_content_type="text/markdown",
  long_description=read_md('README.md'),
  author = 'Thomas Konstantinovsky',
  author_email = 'thomaskon90@gmail.com',
  url = 'https://github.com/MuteJester/PyNCBI',
  download_url = 'https://github.com/MuteJester/PyNCBI/archive/refs/tags/0.1.6.1.tar.gz',
  keywords = ['api','data database','analytics,biology','methylation','epigenetics','ncbi','epigenetic-data'],   # Keywords that define your package best
    install_requires=[
        'methylprep',
        'wget',
        'tqdm',
        'pandas',
        'termcolor',
    ],
  classifiers=[
    'Development Status :: 3 - Alpha',
    'Intended Audience :: Developers',
    'Topic :: Scientific/Engineering :: Information Analysis',
    'License :: OSI Approved :: MIT License',
    'Programming Language :: Python :: 3.8',
    'Programming Language :: Python :: 3.9',
  ],
)
