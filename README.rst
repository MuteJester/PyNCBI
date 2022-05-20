.. raw:: html

   <p align="center">

|Stargazers| |Commits| |Issues| |MIT License| |LinkedIn|

.. raw:: html

   </p>

.. raw:: html

   <!-- PROJECT LOGO -->

.. raw:: html

   <p align="center">

.. raw:: html

   <h2 align="center">

PyNCBI

.. raw:: html

   </h2>

.. raw:: html

   <p align="center">

Simple API for Python Integration with NCBI . Explore the docs » View
Demo · Report Bug · Request Feature

.. raw:: html

   </p>

.. raw:: html

   </p>

.. raw:: html

   <!-- TABLE OF CONTENTS -->

Table of Contents
-----------------

-  `About the Project <#about-the-project>`__
-  `Usage <#usage>`__
-  `Roadmap <#roadmap>`__
-  `Contributing <#contributing>`__
-  `License <#license>`__
-  `Contact <#contact>`__

.. raw:: html

   <!-- ABOUT THE PROJECT -->

About The Project
-----------------

Here’s Why PyNCBI :dna: :

When working with methylation data, NCBI might be one of the most
extensive open source databases that provide the methylation data and
the information around it. When working with NCBI on a day-to-day basis,
searching, querying, and extracting information may prove to be a
time-consuming and headache-producing challenge. PyNCBI strives to
answer all needs a researcher might need when communicating with NCBI
using a straightforward python API that allows to quickly test, extract,
analyze and download relevant data.

Installation
~~~~~~~~~~~~

::

   pip install PyNCBI

.. raw:: html

   <!-- USAGE EXAMPLES -->

Usage
-----

GSE Wise Info Retrival
^^^^^^^^^^^^^^^^^^^^^^

.. code:: py

   from PyNCBI import GEOReader
   # Create Reader Instance
   reader= GEOReader()
   # Extact all GSM info associated to GSE99624 (Saved csv will appear in your downloads folder)
   reader.extract_gse_sample_info('GSE99624')

GSE Wise Data Retrival
^^^^^^^^^^^^^^^^^^^^^^

.. code:: py

   from PyNCBI import GEOReader
   # Create Reader Instance
   reader= GEOReader()
   # Extact all GSM methylation data associated to GSE142512 (Saved files will appear in your downloads folder per GSM depending on page data status)
   reader.download_gse_data('GSE142512')

Single GSM Data Retrival
^^^^^^^^^^^^^^^^^^^^^^^^

.. code:: py

   from PyNCBI import GEOReader
   # Create Reader Instance
   reader= GEOReader()
   # Extact GSM methylation data associated to GSE142512 (Saved file will appear in your downloads folder per GSM depending on page data status)
   reader.download_gsm_data('GSM1518180')

Parsing IDAT files
^^^^^^^^^^^^^^^^^^

.. code:: py

   from PyNCBI import parse_idat_files

   # Beta Values will be stored in a parquet file in path
   parse_idat_files("Path_To_IDAT_FILES/", 'array_type')

Currently Supported Data Features
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

-  **GSE Wise Card Information Extraction**
-  **GSM Card Information Extraction**
-  **GSE Wise Methylation Data Extraction**
-  **GSM Card Methylation Data Extraction**
-  **IDAT File Parsing Management Based on methylprep**

.. raw:: html

   <!-- ROADMAP -->

Roadmap
-------

See the `open issues <https://github.com/MuteJester/PyNCBI/issues>`__
for a list of proposed features (and known issues).

.. raw:: html

   <!-- CONTRIBUTING -->

Contributing
------------

Contributions are what make the open-source community such a powerful
place to create new ideas, inspire, and make progress. Any contributions
you make are **greatly appreciated**.

1. Fork the Project
2. Create your Feature Branch
   (``git checkout -b feature/AmazingFeature``)
3. Commit your changes (``git commit -m 'Add some AmazingFeature'``)
4. Push to the Branch (``git push origin feature/AmazingFeature``)
5. Open a Pull Request

.. raw:: html

   <!-- LICENSE -->

License
-------

Distributed under the MIT license. See ``LICENSE`` for more information.

.. raw:: html

   <!-- CONTACT -->

Contact
-------

`Thomas Konstantinovsky <>`__ - thomaskon90@gmail.com

Project Link: https://github.com/MuteJester/PyNCBI

.. raw:: html

   <!-- MARKDOWN LINKS & IMAGES -->

.. |Stargazers| image:: https://img.shields.io/github/stars/MuteJester/PyNCBI.svg?style=flat-square
   :target: https://github.com/MuteJester/PyNCBI/stargazers
.. |Commits| image:: https://img.shields.io/github/commit-activity/m/MuteJester/PyNCBI?style=flat-square
   :target: https://github.com/MuteJester/PyNCBI
.. |Issues| image:: https://img.shields.io/github/issues/MuteJester/PyNCBI.svg?style=flat-square
   :target: https://github.com/MuteJester/PyNCBI/issues
.. |MIT License| image:: https://img.shields.io/github/license/MuteJester/PyNCBI.svg?style=flat-square
   :target: https://github.com/MuteJester/PyNCBI/blob/master/LICENSE
.. |LinkedIn| image:: https://img.shields.io/badge/-LinkedIn-black.svg?style=flat-square&logo=linkedin&colorB=555
   :target: https://www.linkedin.com/in/thomas-konstantinovsky-56230117b/
