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
    <img src="assets/pyncbi_logo.png" alt="Logo" width="480" height="230">
  </a>

  <h2 align="center">PyNCBI</h2>

  <p align="center">
    Simple API for Python Integration with  <a href=https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=><strong>NCBI GEO</strong></a>  .
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

- [About the Project](#about-the-project)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage)
  - [GSM (Gene Sample Microarray)](#gsm-gene-sample-microarray)
  - [GSE (Gene Series Expression)](#gse-gene-series-expression)
  - [GEOReader (Low-Level API)](#georeader-low-level-api)
- [Configuration](#configuration)
- [Logging](#logging)
- [Exception Handling](#exception-handling)
- [Features](#features)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)
- [Contact](#contact)



<!-- ABOUT THE PROJECT -->
## About The Project

**PyNCBI** provides a clean Python API for accessing DNA methylation data from NCBI's Gene Expression Omnibus (GEO) database.

### Why PyNCBI?

- **Simple API**: Fetch methylation data with just a few lines of code
- **Automatic Caching**: Downloaded data is cached locally for fast subsequent access
- **Type-Safe**: Full type hints and modern Python 3.11+ support
- **Robust Error Handling**: Descriptive exceptions with helpful hints
- **Configurable Logging**: Control verbosity with colored output
- **Multiple Fetch Modes**: Choose between per-sample or supplementary file downloads



## Installation

```bash
pip install PyNCBI
```

### Requirements

- Python 3.11+
- pandas
- numpy
- requests
- tqdm
- methylprep (for IDAT file processing)



## Quick Start

```python
from PyNCBI import GSM, GSE

# Fetch a single sample
gsm = GSM('GSM1518180')
print(gsm.info)
print(gsm.data)

# Fetch an entire series
gse = GSE('GSE85506', mode='supp')
print(f"Found {len(gse)} samples")
```



## Usage

### GSM (Gene Sample Microarray)

<img src="assets/GSM_Structure.png" alt="GSM Structure" width="300" height="230">

The `GSM` class represents a single sample with its methylation data and metadata.

#### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `info` | `pd.Series` | Complete sample metadata |
| `data` | `pd.DataFrame` | Probe IDs and beta values |
| `characteristics` | `dict` | Parsed sample characteristics |
| `array_type` | `str` | Methylation array platform |
| `gse` | `str` | Parent series ID |

#### Basic Usage

```python
from PyNCBI import GSM

# Fetch and cache a sample
gsm = GSM('GSM1518180')
print(gsm)
```
```
GSM: GSM1518180 | GSE: GSE62003
tissue:  Whole blood
Sex:  Male
age:  77
```

#### Accessing Data

```python
# Get methylation beta values
beta_values = gsm.data
print(f"Probes: {len(beta_values)}")

# Get sample metadata
print(gsm.info['!Sample_source_name_ch1'])

# Get parsed characteristics
print(gsm.characteristics)
# {'tissue': 'Whole blood', 'Sex': 'Male', 'age': '77'}
```

#### Metadata Only (No Data Download)

```python
# Fetch only metadata without downloading methylation data
gsm = GSM('GSM1518180', shell_only=True)
print(gsm.info)  # Available
print(gsm.data)  # None - no data downloaded
```


### GSE (Gene Series Expression)

<img src="assets/GSE_Structure.png" alt="GSE Structure" width="550" height="230">

The `GSE` class represents a collection of samples from an experiment.

#### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `info` | `pd.Series` | Series metadata |
| `gsms` | `dict[str, GSM]` | Dictionary of GSM objects |

#### Fetch Modes

| Mode | Description | Best For |
|------|-------------|----------|
| `'per_gsm'` | Fetch each sample individually | Small datasets, reliability |
| `'supp'` | Use supplementary tar file | Large datasets, speed |

#### Using Supplementary Files (Recommended for Large Datasets)

```python
from PyNCBI import GSE

# Fetch using supplementary file - faster for large datasets
gse = GSE('GSE85506', mode='supp', file_index=0)  # Use first supplementary file
print(gse)
```
```
GSE: GSE85506
Array Type: GPL13534 (450k)
Number of Samples: 47
Title: DNA methylation analysis in women with fibromyalgia
```

The `file_index` parameter selects which supplementary file to use (0-indexed). This avoids interactive prompts for batch processing.

#### Using Per-GSM Mode

```python
# Fetch each sample individually - more reliable but slower
gse = GSE('GSE62003', mode='per_gsm')
```

#### Accessing Samples

```python
# Access a specific sample
gsm = gse['GSM2267972']
print(gsm.data)

# Iterate over all samples
for gsm_id, gsm in gse.items():
    print(f"{gsm_id}: {len(gsm.data)} probes")

# Get all sample IDs
print(list(gse.keys()))
```

#### Using FetchMode Enum (Type-Safe)

```python
from PyNCBI import GSE, FetchMode

# Use enum instead of string for type safety
gse = GSE('GSE85506', mode=FetchMode.SUPPLEMENTARY, file_index=0)

# Equivalent to mode='supp'
```


### GEOReader (Low-Level API)

For direct interaction with NCBI GEO without caching.

```python
from PyNCBI import GEOReader

reader = GEOReader()

# Extract metadata for a single sample
gsm_info = reader.extract_gsm_info('GSM1518180')

# Extract metadata for all samples in a series
gse_info = reader.extract_gse_sample_info('GSE62003')

# List samples in a series
gsm_ids = reader.list_gse_samples('GSE62003')
print(f"Found {len(gsm_ids)} samples")

# Check data availability
status = reader.get_gsm_data_status('GSM1518180')
# 0 = data on page, 1 = IDAT files, -1 = no data
```



## Configuration

PyNCBI can be configured via environment variables or programmatically.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PYNCBI_CACHE_FOLDER` | `~/.pyncbi/cache` | Cache directory |
| `PYNCBI_REQUEST_TIMEOUT` | `30` | HTTP timeout in seconds |
| `PYNCBI_LOG_LEVEL` | `INFO` | Logging level |

### Programmatic Configuration

```python
from PyNCBI import get_config, set_config, Config

# View current configuration
config = get_config()
print(config.cache_folder)
print(config.request_timeout)

# Update configuration
set_config(Config(
    cache_folder='/custom/cache/path',
    request_timeout=60.0
))
```



## Logging

PyNCBI includes a configurable logging system with colored output.

### Quick Setup

```python
from PyNCBI import configure_logging, silence, verbose, LogLevel

# Enable verbose output
verbose()

# Silence all output
silence()

# Set specific level
configure_logging(level=LogLevel.DEBUG)
```

### Temporary Log Level

```python
from PyNCBI import log_level, LogLevel, GSM

# Temporarily change log level
with log_level(LogLevel.DEBUG):
    gsm = GSM('GSM1518180')  # Verbose output

# Back to normal level
gsm2 = GSM('GSM1518181')  # Normal output
```

### Log Levels

| Level | Description |
|-------|-------------|
| `LogLevel.DEBUG` | Detailed debugging information |
| `LogLevel.INFO` | General progress information |
| `LogLevel.WARNING` | Warnings about potential issues |
| `LogLevel.ERROR` | Error messages |
| `LogLevel.CRITICAL` | Critical errors |
| `LogLevel.SILENT` | Suppress all output |

### Environment Variable

```bash
# Set log level via environment
export PYNCBI_LOG_LEVEL=DEBUG
```



## Exception Handling

PyNCBI provides a comprehensive exception hierarchy for robust error handling.

### Exception Hierarchy

```
PyNCBIError (base)
├── NetworkError
│   ├── ConnectionFailedError
│   ├── RequestTimeoutError
│   ├── HTTPError
│   └── DownloadError
├── DataError
│   ├── ParseError / SOFTParseError
│   ├── NoDataAvailableError
│   ├── InvalidAccessionError
│   └── DataProcessingError
├── CacheError
│   ├── CacheCorruptedError
│   ├── CacheNotFoundError
│   └── CacheWriteError
└── ConfigurationError
    ├── InvalidModeError
    └── UnsupportedArrayTypeError
```

### Basic Error Handling

```python
from PyNCBI import GSM
from PyNCBI.exceptions import PyNCBIError, NoDataAvailableError, NetworkError

try:
    gsm = GSM('GSM123456')
except NoDataAvailableError as e:
    print(f"No data available for {e.accession}")
    print(f"Hint: {e.hint}")
except NetworkError as e:
    print(f"Network error: {e.message}")
    print(f"URL: {e.url}")
except PyNCBIError as e:
    print(f"PyNCBI error: {e.message}")
```

### Handling Transient Errors with Retry

```python
from PyNCBI import GSM
from PyNCBI.exceptions import TRANSIENT_ERRORS
import time

def fetch_with_retry(gsm_id, max_retries=3):
    for attempt in range(max_retries):
        try:
            return GSM(gsm_id)
        except TRANSIENT_ERRORS as e:
            if attempt < max_retries - 1:
                print(f"Attempt {attempt + 1} failed, retrying...")
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                raise
```

### Exception Groups

| Group | Exceptions | Use Case |
|-------|------------|----------|
| `TRANSIENT_ERRORS` | Connection, Timeout, HTTP | Worth retrying |
| `USER_ERRORS` | InvalidAccession, InvalidMode | Fix user input |
| `DATA_ERRORS` | NoData, ParseError, Processing | Data issues |



## Features

### Currently Supported

- GSE and GSM card information extraction
- Methylation beta value download and parsing
- Multiple fetch modes (per_gsm, supplementary)
- IDAT file processing via methylprep
- Automatic caching with inspection API
- Configurable logging with colors
- Comprehensive exception hierarchy
- Full type hints (py.typed)

### Supported Platforms

| Platform | Array Type | Description |
|----------|------------|-------------|
| GPL8490 | 27k | Illumina HumanMethylation27 |
| GPL13534 | 450k | Illumina HumanMethylation450 |
| GPL16304 | 450k | (Alternative) |
| GPL21145 | EPIC | Illumina MethylationEPIC |
| GPL23976 | EPIC+ | Illumina MethylationEPIC v2.0 |



<!-- ROADMAP -->
## Roadmap

See the [open issues](https://github.com/MuteJester/PyNCBI/issues) for a list of proposed features and known issues.

### Planned Features

- [ ] Async/concurrent downloads
- [ ] Additional array platform support
- [ ] Data quality metrics
- [ ] Export to various formats



<!-- CONTRIBUTING -->
## Contributing

Contributions are what make the open-source community such a powerful place to create new ideas, inspire, and make progress. Any contributions you make are **greatly appreciated**.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

### Development Setup

```bash
# Clone the repository
git clone https://github.com/MuteJester/PyNCBI.git
cd PyNCBI

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Run type checking
mypy src/PyNCBI/

# Run linting
ruff check src/PyNCBI/
```



<!-- LICENSE -->
## License

Distributed under the MIT license. See `LICENSE` for more information.



<!-- CONTACT -->
## Contact

[Thomas Konstantinovsky](https://www.linkedin.com/in/thomas-konstantinovsky-56230117b/) - thomaskon90@gmail.com

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
