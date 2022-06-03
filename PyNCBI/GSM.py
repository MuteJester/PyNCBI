import os
import pickle
import shutil
from functools import cached_property
from uuid import uuid4
import pandas as pd
from termcolor import colored

from PyNCBI.Constants import NCBI_QUERY_URL, CACHE_FOLDER, ARRAY_TYPES

from PyNCBI.Utilities import get_data_locally, parse_characteristics, compress_and_store, load_and_decompress

from PyNCBI.FileUtilities import parse_idat_files

from PyNCBI import gsm_page_data_status, download_gsm_data, LOCAL_DOWNLOADS_FOLDER


class GSM:
    """
     A class to represent a single GSM entity.
     The class contains all information and data regarding a single GSM as well as various
     functionality for simple work and management and manipulation of GSM entities.

     Attributes
     ----------
     gsm_id : str
         the GSM identifier as it appears on a NCBI (https://www.ncbi.nlm.nih.gov/geo/) page.
    shell_only : bool (default False)
        If True than only the card information will be extracted i.e methylation beta values extraction will be
        skipped.
    overwrite_cache: bool (deafault False)
        If True than the cached version will be overwritten with by a fresh copy

     Methods
     -------
     store_cache():
         This method Caches the current instance's status in memory for future loading.
         This method is ran automatically on first initialization of a class with a GSM id
     load_cache():
        This method load a cache version of a GSM class instance based on the gsm_id attribute.
     """
    def __init__(self, gsm_id,shell_only = False,overwrite_cache=False):
        # Variables
        # The GSM ID
        self.gsm_id = gsm_id
        if self.is_cached() and not overwrite_cache:
            self.load_cache()
        else:
            # the array type of the GSM
            self.array_type = None
            # The parent GSE ID
            self.gse = None
            # GSM card info
            self.info = None
            # GSM card Data
            self.data = 'Only Info Available'
            # GSM info characteristics section
            self.characteristics = None
            # Extract Info
            self.__extract_info()
            # parse characteristics
            self.characteristics = parse_characteristics([self.info['characteristics_ch1']]).iloc[0]
            self.array_type = self.info['platform_id']
            self.gse = self.info['series_id']
            # skip data extraction if shell only requested
            if not shell_only:
                # Extract data and fill class parameters
                self.__populate_class()
            # Store cache
            self.store_cache()

    def is_cached(self):
        """
        This method checks if this GSM has previously been loaded and cached in memory
        :return:
        """
        return True if f'{self.gsm_id}.ch' in os.listdir(CACHE_FOLDER) else False

    def store_cache(self):
        """
        This method will cache all class attributes
        :return:
        """
        cache = {
            'gsm_id': self.gsm_id,
            'array_type': self.array_type,
            'gse': self.gse,
            'info': self.info,
            'data': self.data,
            'characteristics': self.characteristics
        }

        compress_and_store(cache,CACHE_FOLDER+self.gsm_id+'.ch')

    def load_cache(self):
        """
        This method will load cached instance of the given gsm_id
        :return:
        """
        cache = load_and_decompress(CACHE_FOLDER + self.gsm_id + '.ch')
        self.gsm_id = cache['gsm_id']
        self.array_type = cache['array_type']
        self.gse = cache['gse']
        self.info = cache['info']
        self.data = cache['data']
        self.characteristics = cache['characteristics']

    def __populate_class(self):
        """
        This method will check if there is an option to extract data from an NCBI GSM card, if there is indeed
        data available it will download and set it to the "data" class attribute
        :return:
        """

        # check page status
        data_status = gsm_page_data_status(self.gsm_id)

        if data_status == -1:
            # raise error in case there is no data to populate the class with
            raise ConnectionError('No Data Available on GSM card')



        files_names = download_gsm_data(self.gsm_id,to_path=CACHE_FOLDER,return_file_names=True)

        if data_status == 0:
            # load data
            self.data = pd.read_csv(CACHE_FOLDER + files_names[0], index_col=0)
            # remove downloaded csv file
            os.remove(CACHE_FOLDER + files_names[0])
        elif data_status == 1:
            # insert red/grn files into folder
            temp_folder_name = str(uuid4())
            os.makedirs(CACHE_FOLDER + temp_folder_name)
            os.rename(CACHE_FOLDER + files_names[0], CACHE_FOLDER + temp_folder_name+'/' + files_names[0])
            os.rename(CACHE_FOLDER + files_names[1], CACHE_FOLDER + temp_folder_name+'/' + files_names[1])
            # run extraction
            parse_idat_files(CACHE_FOLDER + temp_folder_name+'/', ARRAY_TYPES[self.array_type])
            # load data
            self.data = pd.read_parquet(CACHE_FOLDER + temp_folder_name + '/parsed_beta_values.parquet')
            # delete folder
            shutil.rmtree(CACHE_FOLDER + temp_folder_name+'/', )

    def __extract_info(self):
        """
        This method will extract the info from an NCBI GSM card and set it as the "info" attribute
        :return:
        """
        # send query requests to NCBI server
        response = get_data_locally(f'{NCBI_QUERY_URL}{self.gsm_id}&targ=self&form=text&view=quick')

        data = response.split('\n')
        # remove redundant lines
        data = [i for i in data if '!Sample_' in i or '^SAMPLE' in i]
        # remove prefix
        data = [i.replace('!Sample_', '') for i in data]

        data = [i for i in data if len(i) > 0]
        # compact data keys to dict
        processed_data = dict()
        for r in data:
            # skip anomalies
            if len(r.split('=')) != 2:
                continue
            k, v = r.split('=')
            k = k.strip()
            v = v.strip()
            if k in processed_data:
                processed_data[k].append(v)
            else:
                processed_data[k] = [v]

        # collapse list into a single string
        for key in processed_data:
            processed_data[key] = '\n'.join(processed_data[key])

        processed_data = pd.Series(processed_data)
        self.info = processed_data
        # set name to pandas Series
        self.info.name = self.gsm_id

    def __repr__(self):
        str_fmt = f"GSM: {colored(self.gsm_id, color='green', attrs=['bold'])}\
         | GSE: {colored(self.gse, color='green', attrs=['bold'])}\n"
        for key in self.characteristics.index:
            str_fmt = str_fmt + key + \
                      f":  {colored(self.characteristics[key], color='green', attrs=['bold'])}\n"

        print(str_fmt)
        return ""

    def __str__(self):
        str_fmt = f'GSM: {self.gsm_id} | GSE: {self.gse}\n'
        for key in self.characteristics.index:
            str_fmt = str_fmt+key+f':  {self.characteristics[key]}\n'

        return str_fmt

    def __eq__(self, other):
        return True if self.gsm_id == other.gsm_id else False

    def __getitem__(self, cg):
        return self.data.loc[cg,self.gsm_id]
