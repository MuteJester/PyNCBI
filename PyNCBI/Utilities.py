import gzip
import io
import os
import pickle
import re
import shutil
import sys
import zlib
from io import StringIO
from pathlib import Path

import numpy as np
from tqdm.auto import tqdm
import pandas as pd
import requests
import wget
from PyNCBI.Constants import NCBI_QUERY_URL, NCBI_QUERY_BASE_URL, LOCAL_DOWNLOADS_FOLDER


def parse_characteristics(char_section, indices=None):
    """
    This function will take in a characteristics_ch1 section from a GSM soft format
    and split it into a dataframe where each column is a characteristic
    :param char_section: characteristics_ch1 section string
    :param indices: the index value of the returned DataFrame (optional)
    :return:
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

    if indices is None:
        return pd.DataFrame(jdict)
    else:
        rdf = pd.DataFrame(jdict)
        rdf.index = indices
        return rdf


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
        data_stream = requests.get(link, stream=True)
        return data_stream.content.decode('utf-8')
    except Exception as e:
        print('There Was an Error while downloading file from: ', link)
        print('Exception: ', e)
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
            aux += 1


def gunzip_shutil(source_filepath, dest_filepath, block_size=65536):
    with gzip.open(source_filepath, 'rb') as s_file, \
            open(dest_filepath, 'wb') as d_file:
        shutil.copyfileobj(s_file, d_file, block_size)


def gsm_page_data_status(gsm):
    """
    This function checks the format of data availability a GSM card has, it can be either the data is present
    on page, meaning all the probe values can be extracted from the card itself,
    or there can be idat files (red and green) which need to be than parsed via an idat reader.
    In some cases there is no data available in which case the appropriate response will return

    :return: 0 - Data on page,
             1 - IDAT files,
            -1 - no data available
    """

    soft_link = f'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gsm}&targ=self&form=text&view=quick'
    gsm_soft = get_data_locally(soft_link)

    sup_file = re.findall(r'!Sample_supplementary_file = .+', gsm_soft)

    # conditions for gsm card being classified as "has idat files"
    cond_idat = [len(sup_file) == 2, 'idat' in sup_file[0]]

    # conditions for gsm card being classified as "has data"
    rowcount = re.search(r'!Sample_data_row_count = [0-9]+', gsm_soft).group()[25:]
    rowcount = int(rowcount)
    cond_data_on_page = [len(sup_file) == 1, ('NONE' in sup_file[0] or ('Red' not in sup_file[0])), rowcount > 0]
    if all(cond_idat):
        return 1
    elif all(cond_data_on_page):
        return 0
    else:
        return -1


def download_gsm_data(gsm, to_path=None,return_file_names = False):
    """
    This function will download the methylation data present on the GSM card depending on that cards methylation
    data status
    :param gsm: the target GSM id
    :to_path: the path in which the downloaded data will be saved, default is local downloads folder
    :return_file_names: wheter to return the saved files names or not
    :return:
    """
    data_status = gsm_page_data_status(gsm)

    # idat clause
    if data_status == 1:
        # get soft data for gsm card
        soft_link = f'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={gsm}&targ=self&form=text&view=quick'
        gsm_soft = get_data_locally(soft_link)
        # find and process FTP urls
        sup_file = re.findall(r'!Sample_supplementary_file = .+', gsm_soft)
        sup_file = [i[29:-1] for i in sup_file]

        # download ftp green and red gz files
        for url in tqdm(sup_file):
            save_path = LOCAL_DOWNLOADS_FOLDER if to_path is None else to_path
            wget.download(url=url, out=save_path + url.split('/')[-1], bar=progress_bar)
            # check if file is tar zipped
            if '.gz' in url.split('/')[-1]:
                # unpack and delete zipped version
                gunzip_shutil(save_path + url.split('/')[-1], save_path + url.split('/')[-1][:-3])
                # delete zipped file
                os.remove(save_path + url.split('/')[-1])
        if return_file_names:
            return [sup_file[0].split('/')[-1][:-3],sup_file[1].split('/')[-1][:-3]]
    # data on page clause
    elif data_status == 0:
        page_data_url = NCBI_QUERY_BASE_URL + f'?acc={gsm}&targ=self&form=text&view=data'
        probe_data = get_data_locally(page_data_url)
        # remove header
        data_start = gsm_data_file_table_start(probe_data)
        probe_data = probe_data.split('\n')
        probe_data = '\n'.join(probe_data[data_start:-2])
        # convert to dataframe
        probe_data = pd.read_table(StringIO(probe_data))
        # process dataframe
        probe_data = probe_data.rename(columns={'ID_REF': 'probe', 'VALUE': gsm})
        probe_data[gsm] = probe_data[gsm].astype(np.float32)
        if to_path is not None:
            probe_data.to_csv(to_path + gsm + '.csv', index=False)
        else:
            probe_data.to_csv(LOCAL_DOWNLOADS_FOLDER + gsm + '.csv', index=False)

        if return_file_names:
            return [gsm + '.csv']

    else:
        print(f'No Data Exists on GSM Card for {gsm}')

def compress_and_store(data,path):
    """
    this function will compress the information passed in the data variable and store it in path
    :param data:
    :param path:
    :return:
    """
    bytes = io.BytesIO()
    pickle.dump(data,bytes)
    zbytes = zlib.compress(bytes.getbuffer())
    with open(path,'wb') as handler:
        handler.write(zbytes)

def load_and_decompress(path):
    """
    this function will load the compressed file saved at path and decompress it
    :param path:
    :return:
    """
    with open(path,'rb') as handler:
        zbtyes = handler.read()
    bytes =  zlib.decompress(zbtyes)
    return pickle.loads(bytes)