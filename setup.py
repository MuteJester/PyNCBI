from setuptools import setup, find_packages


setup(
    name='PyNCBI',
    version='0.1.1',
    license='MIT',
    author="Thomas Konstantinovsky",
    author_email='thomaskon90@gmail.com',
    packages=find_packages('PyNCBI'),
    package_dir={'': 'PyNCBI'},
    url='https://github.com/MuteJester/PyNCBI',
    keywords='api,data database,analytics,biology,methylation,epigenetics,ncbi,epigenetic-data',
    install_requires=[
        'methylprep',
        'wget',
        'tqdm',
        'pandas'
      ],

)