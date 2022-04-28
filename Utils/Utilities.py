import os
import re
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm
# Utils
from Utils.Constants import NCBI_QUERY_URL


def parse_characteristics(char_section):
    """
    This function will take in a characteristics_ch1 section from a GSM soft format
    and split it into a dataframe where each column is a characteristic
    """
    jsns = []
    for i in char_section:
        jsns.append(i.split('\n'))

    jdict = []
    for i in jsns:
        d = dict()
        for j in i:
            k, v = j.split(':')
            v = v.strip()
            d[k] = v
        jdict.append(d)
    return pd.DataFrame(jdict)


def gse_of_gsm(gsm_number):
    """
    This function takes a GSM number and uses NCBI INFO PAGE to retrive the appropriate GSE number
    """
    page_content = requests.get(NCBI_QUERY_URL + gsm_number)
    if page_content.status_code != 200:
        raise ValueError(f'NCBI Query for {gsm_number} Has Returned Bad Status Code')
    GSES = re.findall(r'GSE[0-9]+', page_content.text)
    # Remove Duplicates
    GSES = list(set(GSES))
    return GSES[0] if len(GSES) == 1 else GSES


def platform_of_gsm(gsm_number):
    """
    This function takes a GSM number and uses NCBI INFO PAGE to retrive the platform type (chip type)
    """
    page_content = requests.get(NCBI_QUERY_URL + gsm_number)
    if page_content.status_code != 200:
        raise ValueError(f'NCBI Query for {gsm_number} Has Returned Bad Status Code')
    PLATFORM = re.findall(r'GPL[0-9]+', page_content.text)
    # Remove Duplicates
    PLATFORM = list(set(PLATFORM))
    return PLATFORM[0] if len(PLATFORM) == 1 else PLATFORM


def is_info_dataframe_in_downloads(gse):
    file_path = str(Path.home()) + '/Downloads/'
    if gse + '_INFO.csv' in os.listdir(file_path):
        return True
    else:
        return False

