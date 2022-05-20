import os
import re
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import wget
from tqdm.auto import tqdm
from PyNCBI.Constants import NCBI_QUERY_URL, NCBI_QUERY_BASE_URL
from PyNCBI.Utilities import is_info_dataframe_in_downloads, get_data_locally, progress_bar, \
    gsm_data_file_table_start, gunzip_shutil, gsm_page_data_status, download_gsm_data


class GEOReader:
    """
      A connector class to a REST type API to NCBI's GEO via get requests used to inference, interact and
      scrape GSM/GSE/PLATFORM Tickets
      ...

      Attributes
      ----------

      Methods
      -------
      parse_gsm_soft(gsm,remove_trace)
        this function parses the corresponding SOFT file to the passed gsm name from your local /Downloads folder
      gsms_from_gse_soft(gse, remove_trace)
        this function uses a GSE SOFT file to extract all GSM numbers corresponding to that GSE
      extract_gsm_data(gsm,verbose)
        Extract data of a single GSM by downloading its SOFT file and parsing it
      extract_gse_sample_info(gse):
          Iterate over all GSM's associated with a given GSE and extract all information available
            on those GSM's on NCBI

    """

    def __init__(self):
        pass
        self.download_folder = str(Path.home()) + '/Downloads/'

    def parse_gsm_soft(self, gsm):
        """
        This function will extract the data from a string containing soft data of a GSM card
        as it is on NCBI
        """
        data = gsm.split('\n')
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
        return processed_data

    def gsms_from_gse_soft(self, gse, remove_trace=True):
        """
        This function extract the list of all GSMS associated with a GSE based on the gse's
        "soft" text file as it is on NCBI
        """
        data = gse
        data = data.split('\n')
        data = [i.split('=')[1].strip() for i in data if '!Series_sample_id' in i]
        return data

    def extract_gsm_info(self, gsm, verbose=False):
        """
        Extract data of a single GSM by downloading its SOFT file and parsing it
        """

        # send query requests to NCBI server
        response = get_data_locally(f'{NCBI_QUERY_URL}{gsm}&targ=self&form=text&view=quick')

        gsm_info = self.parse_gsm_soft(response)
        if verbose:
            print(gsm, ' Info Extracted Successfully')
        return gsm_info

    def extract_gse_sample_info(self, gse):
        """
        Iterate over all GSM's associated with a given GSE and extract all information available
        on those GSM's on NCBI
        """

        # check if info file already exists
        if is_info_dataframe_in_downloads(gse):
            return pd.read_csv(str(Path.home()) + '/Downloads/' + gse + '_INFO.csv')

        # send query requests to NCBI server
        response = get_data_locally(f'{NCBI_QUERY_URL}{gse}&targ=self&form=text&view=quick')

        GSMS = self.gsms_from_gse_soft(response)

        # tqdm iterator
        itr = tqdm(GSMS, leave=False, position=0)
        retrived_data = []
        for gsm in itr:
            retrived_data.append(self.extract_gsm_info(gsm))
            itr.set_postfix({'Last GSM Extracted: ': gsm})

        print('Finished GSM Info Extraction')

        dataframe_info = pd.concat(retrived_data, axis=1).T
        dataframe_info = dataframe_info.set_index('^SAMPLE')

        dataframe_info.to_csv(self.download_folder + gse + '_INFO.csv')
        print('Saved: ' + '/Downloads/' + gse + '_INFO.csv')

        return dataframe_info

    def download_gse_data(self, gse, to_path=None):
        """
        this function will iterate over all GSMs associated to a given GSE and download each GSM
        card methylation data
        :param gse:
        :return:
        """
        # send query requests to NCBI server
        response = get_data_locally(f'{NCBI_QUERY_URL}{gse}&targ=self&form=text&view=quick')
        GSMS = self.gsms_from_gse_soft(response)

        iterator = tqdm(GSMS, position=0)
        for gsm in iterator:
            download_gsm_data(gsm, to_path)
            iterator.set_postfix({'Status: ': f'Downloaded {gsm} Successfully'})
