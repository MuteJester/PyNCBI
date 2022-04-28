# PyNCBI
A Python API library for exploration and data retrieval of data from NCBI


## Example of GSE Data Retrival

```
# Create Reader Instance
reader= GEOReader()
# Extact all GSM info associated to GSE99624 (Saved csv will appear in your downloads folder)
reader.extract_gse_sample_info('GSE99624')
```
