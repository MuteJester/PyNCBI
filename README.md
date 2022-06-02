<p align="center">

[![Stargazers][stars-shield]][stars-url]
[![Commits][commits-shield]][commits-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]
[![LinkedIn][linkedin-shield]][linkedin-url]

</p>


<!-- PROJECT LOGO -->
<br />
<p align="center">
  <a href="https://github.com/MuteJester/PyNCBI">
    <img src="PyNCBI/Repo%20Misc/pyncbi_logo.png" alt="Logo" width="480" height="230">
  </a>

  <h2 align="center">PyNCBI</h2>

  <p align="center">
    Simple API for Python Integration with  <a href=https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=><strong>NCBI</strong></a>  .
    <br />
    <a href="https://pyncbi.readthedocs.io/"><strong>Explore the docs »</strong></a>
    <br />
    <br />
    <a href="https://github.com/MuteJester/PyNCBI/wiki/">View Demo</a>
    ·
    <a href="https://github.com/MuteJester/PyNCBI/issues">Report Bug</a>
    ·
    <a href="https://github.com/MuteJester/PyNCBI/issues">Request Feature</a>
  </p>
</p>



<!-- TABLE OF CONTENTS -->
## Table of Contents

* [About the Project](#about-the-project)
* [Usage](#usage)
* [Roadmap](#roadmap)
* [Contributing](#contributing)
* [License](#license)
* [Contact](#contact)



<!-- ABOUT THE PROJECT -->
## About The Project
Here's Why PyNCBI :dna: :

When working with methylation data, NCBI might be one of the most extensive open source databases that provide the methylation data and the information around it.
When working with NCBI on a day-to-day basis, searching, querying, and extracting information may prove to be a time-consuming and headache-producing challenge.
PyNCBI strives to answer all needs a researcher might need when communicating with NCBI using a straightforward python API that allows to quickly test, extract, analyze and download relevant data.



### Installation
```
pip install PyNCBI
```

<!-- USAGE EXAMPLES -->
## Usage

### GEOReader
#####  GSE Wise Info Retrival
```py
from PyNCBI import GEOReader
# Create Reader Instance
reader= GEOReader()
# Extact all GSM info associated to GSE99624 (Saved csv will appear in your downloads folder)
reader.extract_gse_sample_info('GSE99624')
```


#####  GSE Wise Data Retrival
```py
from PyNCBI import GEOReader
# Create Reader Instance
reader= GEOReader()
# Extact all GSM methylation data associated to GSE142512 (Saved files will appear in your downloads folder per GSM depending on page data status)
reader.download_gse_data('GSE142512')
```

#####  Single GSM Data Retrival
```py
from PyNCBI import GEOReader
# Create Reader Instance
reader= GEOReader()
# Extact GSM methylation data associated to GSE142512 (Saved file will appear in your downloads folder per GSM depending on page data status)
reader.download_gsm_data('GSM1518180')
```

#####  Parsing IDAT files

```py
from PyNCBI import parse_idat_files

# Beta Values will be stored in a parquet file in path
parse_idat_files("Path_To_IDAT_FILES/", 'array_type')
```

### GSM
The GSM API extracts all info from a GSM card and downloads the methylation data, and renders the beta values ready for work.
After extracting and preprocessing the data once, that GSM instance will be cached for your convenience; each following time, you will reference the same GSM id the cached version will be loaded.
The GSM class contains the following attributes:
  * **array_type** - the array type used to sequence the data
  * **gse** -  the parent GSM id
  * **info** - a Pandas Series contacting the entire GSM card information
  * **data** - a Pandas DataFrame containing the probes and matching beta values
  * **characteristics** - the parsed characteristics section from the GSM info section

#####  Single GSM API
```py
from PyNCBI import GSM

# Build and populate with data an instance of a GSM container
example_gsm = GSM('GSM1518180')
print(example_gsm)
```
Output:
```
GSM: GSM1518180 | GSE: GSE62003
tissue:  Whole blood
Sex:  Male
age:  77
```

### Currently Supported Data Features
  * __GSE Wise Card Information Extraction__
  * __GSM Card Information Extraction__
  * __GSE Wise Methylation Data Extraction__
  * __GSM Card Methylation Data Extraction__
  * __IDAT File Parsing Management Based on methylprep__
  * __Single GSM API__

 


<!-- ROADMAP -->
## Roadmap

See the [open issues](https://github.com/MuteJester/PyNCBI/issues) for a list of proposed features (and known issues).

<!-- CONTRIBUTING -->
## Contributing


Contributions are what make the open-source community such a powerful place to create new ideas, inspire, and make progress. Any contributions you make are **greatly appreciated**.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request


<!-- LICENSE -->
## License

Distributed under the MIT license. See `LICENSE` for more information.



<!-- CONTACT -->
## Contact

[Thomas Konstantinovsky]() - thomaskon90@gmail.com

Project Link: [https://github.com/MuteJester/PyNCBI](https://github.com/MuteJester/PyNCBI)





<!-- MARKDOWN LINKS & IMAGES -->
[stars-shield]: https://img.shields.io/github/stars/MuteJester/PyNCBI.svg?style=flat-square
[stars-url]: https://github.com/MuteJester/PyNCBI/stargazers
[issues-shield]: https://img.shields.io/github/issues/MuteJester/PyNCBI.svg?style=flat-square
[issues-url]: https://github.com/MuteJester/PyNCBI/issues
[license-shield]: https://img.shields.io/github/license/MuteJester/PyNCBI.svg?style=flat-square
[license-url]: https://github.com/MuteJester/PyNCBI/blob/master/LICENSE
[commits-shield]: https://img.shields.io/github/commit-activity/m/MuteJester/PyNCBI?style=flat-square
[commits-url]: https://github.com/MuteJester/PyNCBI
[linkedin-shield]: https://img.shields.io/badge/-LinkedIn-black.svg?style=flat-square&logo=linkedin&colorB=555
[linkedin-url]: https://www.linkedin.com/in/thomas-konstantinovsky-56230117b/
