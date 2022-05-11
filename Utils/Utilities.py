import gzip
import os
import re
import shutil
import sys
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


def get_data_locally(link):
    """
    this function will download the file provided in the link variable, the link url should be a byte string and be
    the actual location of the file on a server, meaning that a get request will retrieve that file
    :param link: a link the file
    :return: a string containing the test in the file
    """
    try:
        data_stream = requests.get(link,stream=True)
        return data_stream.content.decode('utf-8')
    except Exception as e:
        print('There Was an Error while downloading file from: ',link)
        print('Exception: ',e)
        return None

def progress_bar(current, total, width=80):
  """
  This function is a utility for wget download function
  :param current:
  :param total:
  :param width:
  :return:
  """
  progress_message = "Downloading: %d%% [%d / %d] bytes" % (current / total * 100, current, total)
  sys.stdout.write("\r" + progress_message)
  sys.stdout.flush()


def gsm_data_file_table_start(gsm_file):
    """
    this function takes a text file with methylation array data and find the the line on which the data start
    i.e skips the headers, formally the data start when ID_REF and VALUE are present in the line
    :param gsm_file:
    :return:
    """
    # line counter
    aux = 0
    for line in gsm_file.split('\n'):
        if 'ID_REF\tVALUE' in line:
            return aux
        elif aux > 10:
            raise ValueError('GSM data file start has not been found in first 10 lines')
        else:
            aux+=1

def gunzip_shutil(source_filepath, dest_filepath, block_size=65536):
    with gzip.open(source_filepath, 'rb') as s_file, \
            open(dest_filepath, 'wb') as d_file:
        shutil.copyfileobj(s_file, d_file, block_size)