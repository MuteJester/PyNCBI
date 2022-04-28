import pandas as pd
import numpy as np
# selenium 3
from selenium import webdriver
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
import os
from pathlib import Path
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from tqdm.auto import tqdm
import requests
from Utils.Constants import NCBI_QUERY_URL
from Utils.Utilities import is_info_dataframe_in_downloads


class GEOReader:
    """
      A connector class to a REST type API to NCBI's GEO via get requests used to inference, interact and
      scrape GSM/GSE/PLATFORM Tickets
      ...

      Attributes
      ----------
      headless : bool
          if using browser whether to show selenium driver or not
      browser : bool
          if True than the connection to NCBI will be made via selenium deriver and all commands will be ran
          used selenium if False than the requests library will be used to directly interact with NCBI's REST API

      Methods
      -------
      _go_to_page(url)
          a util function used to navigate selenium webdriver to the given url
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
    def __init__(self, headless=False, browser=False):
        self.browser_mode = browser
        if self.browser_mode:
            if headless:
                chrome_options = Options()
                chrome_options.add_argument("--headless")
                self.browser = webdriver.Chrome(ChromeDriverManager().install(),
                                                options=chrome_options)
            else:
                self.browser = webdriver.Chrome(ChromeDriverManager().install())

    def _go_to_page(self, url):
        if self.browser_mode:
            self.browser.get(url)
        else:
            raise ValueError('Browser Mode is Disabled')

    def parse_gsm_soft(self, gsm, remove_trace=True):
        """
        This function will extract the data from a txt file containing soft data of a GSM card
        as it is on NCBI
        """

        if self.browser_mode:
            with open(str(Path.home()) + f'/Downloads/{gsm}.txt', 'r') as h:
                data = h.read()
                data = data.split('\n')
                # remove redundant lines
                data = [i for i in data if '!Sample_' in i or '^SAMPLE' in i]
                # remove prefix
                data = [i.replace('!Sample_', '') for i in data]

                data = [i for i in data if len(i) > 0]

                # compact data keys to dict
                processed_data = dict()
                for r in data:
                    k, v = r.split('=')
                    k = k.strip()
                    v = v.strip()
                    if k in processed_data:
                        processed_data[k].append(v)
                    else:
                        processed_data[k] = [v]
        else:
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

            # colapse list into a singal string
            for key in processed_data:
                processed_data[key] = '\n'.join(processed_data[key])

        processed_data = pd.Series(processed_data)

        # remove file from local storage
        if self.browser_mode and remove_trace:
            os.remove(str(Path.home()) + f"/Downloads/{gsm}.txt")

        return processed_data

    def gsms_from_gse_soft(self, gse, remove_trace=True):
        """
        This function extract the list of all GSMS associated with a GSE based on the gse's
        "soft" text file as it is on NCBI
        """
        if self.browser_mode:
            with open(str(Path.home()) + f'/Downloads/{gse}.txt', 'r') as h:
                data = h.read()
        else:
            data = gse
        data = data.split('\n')
        data = [i.split('=')[1].strip() for i in data if '!Series_sample_id' in i]

        # remove trace
        if self.browser_mode and remove_trace:
            os.remove(str(Path.home()) + f"/Downloads/{gse}.txt")

        return data

    def extract_gsm_data(self, gsm,verbose=False):
        """
        Extract data of a single GSM by downloading its SOFT file and parsing it
        """

        if self.browser_mode:
            self._go_to_page(NCBI_QUERY_URL + gsm)

            # select SOFT
            wait = WebDriverWait(self.browser, 10)
            selector = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="form"]')))
            selector.click()
            selector.send_keys('S')
            selector.send_keys(Keys.RETURN)
            # Click to download
            btn = self.browser.find_element_by_xpath(
                '//*[@id="ViewOptions"]/table/tbody/tr/td[2]/table/tbody/tr/td/table/tbody/tr/td[2]/img')
            btn.click()
            # wait for file to download
            while (gsm + '.txt') not in os.listdir(str(Path.home()) + '/Downloads/'):
                time.sleep(1)
            print(gsm, ' Soft trace Downloaded...')
            print('Extracting GSM Info')
            gsm_info = self.parse_gsm_soft(gsm)
            print(gsm, ' Info Extracted Successfully')
            return gsm_info
        else:

            # send query requests to NCBI server
            response = requests.get(f'{NCBI_QUERY_URL}{gsm}&targ=self&form=text&view=quick')

            # validate response
            if response.status_code != 200 or 'SAMPLE' not in response.text:
                raise ValueError('Bad Status when Downloading GSM SOFT file')

            gsm_info = self.parse_gsm_soft(response.text, remove_trace=False)
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

        if self.browser_mode:
            self._go_to_page(NCBI_QUERY_URL + gse)

            # select SOFT
            wait = WebDriverWait(self.browser, 10)
            selector = wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="form"]')))
            selector.click()
            selector.send_keys('S')
            selector.send_keys(Keys.RETURN)
            # Click to download
            btn = self.browser.find_element_by_xpath(
                '//*[@id="ViewOptions"]/table/tbody/tr/td[2]/table/tbody/tr/td/table/tbody/tr/td[2]/img')
            btn.click()
            # wait for file to download
            while (gse + '.txt') not in os.listdir(str(Path.home()) + '/Downloads/'):
                time.sleep(1)
            print(gse, ' Soft trace Downloaded...')
        else:
            # send query requests to NCBI server
            response = requests.get(f'{NCBI_QUERY_URL}{gse}&targ=self&form=text&view=quick')

            # validate response
            if response.status_code != 200 or 'SERIES' not in response.text:
                raise ValueError('Bad Status when Downloading GSE SOFT file')

        GSMS = self.gsms_from_gse_soft(response.text)

        # tqdm iterator
        itr = tqdm(GSMS,leave=False,position=0)
        retrived_data = []
        for gsm in itr:
            retrived_data.append(self.extract_gsm_data(gsm))
            itr.set_postfix({'Last GSM Extracted: ': gsm})

        print('Finished GSM Info Extraction')

        dataframe_info = pd.concat(retrived_data, axis=1).T
        dataframe_info = dataframe_info.set_index('^SAMPLE')

        dataframe_info.to_csv(str(Path.home()) + '/Downloads/' + gse + '_INFO.csv')
        print('Saved: ' + '/Downloads/' + gse + '_INFO.csv')

        return dataframe_info


gses = ['GSE106648',
        'GSE107143',
        'GSE111165',
        'GSE111629',
        'GSE41169',
        'GSE50660',
        'GSE52113',
        'GSE52114',
        'GSE52588',
        'GSE53128',
        'GSE53130',
        'GSE64495',
        'GSE67751',
        'GSE77445',
        'GSE84003',
        'GSE85506',
        'GSE87571',
        'GSE99624']
reader = GEOReader()

datas = []
for gse in tqdm(gses):
    rd = reader.extract_gse_sample_info(gse)
