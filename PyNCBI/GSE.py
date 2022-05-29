import os
import shutil
import urllib
from uuid import uuid4
import pandas as pd
import wget
from PyNCBI.FileUtilities import parse_idat_files
from termcolor import colored
from PyNCBI import GSM
from PyNCBI.Constants import NCBI_QUERY_URL, CACHE_FOLDER, ARRAY_TYPES
from tqdm.auto import tqdm
from threading import Thread
from PyNCBI.Utilities import compress_and_store, get_data_locally, gsms_from_gse_soft, parse_and_compress_gse_info, \
    load_and_decompress, chunkify, progress_bar, gunzip_shutil, unzip_tarfile, remove_non_idat_files
from multiprocessing.pool import ThreadPool


class GSE:
    def __init__(self, gse_id, mode, overwrite_cache=False, n_threads=1, remove_gsm_caches=True,shell_only=False):
        self.gse_id = gse_id
        if self.is_cached() and not overwrite_cache:
            self.load_cache()
        else:
            self.GSMS = None
            self.info = None
            self.no_data_GSMS = []

            # Extract GSE Info:
            self.__extract_info()

            if not shell_only:
                # Extract GSM Data
                self.__populate_class(n_threads=n_threads, mode=mode)

            # Cache object state
            self.store_cache()
                # by default remove individual saved caches for each GSM
            if remove_gsm_caches:
                self.remove_gsm_cache()

    def is_cached(self):
        """
        This method checks if this GSE has previously been loaded and cached in memory
        :return:
        """
        return True if f'{self.gse_id}.ch' in os.listdir(CACHE_FOLDER) else False

    def store_cache(self):
        """
        This method will cache all class attributes
        :return:
        """
        cache = {
            'gse_id': self.gse_id,
            'GSMS': self.GSMS,
            'info': self.info,
        }

        compress_and_store(cache, CACHE_FOLDER + self.gse_id + '.ch')

    def load_cache(self):
        """
        This method will load cached instance of the given gse_id
        :return:
        """
        cache = load_and_decompress(CACHE_FOLDER + self.gse_id + '.ch')
        self.gse_id = cache['gse_id']
        self.GSMS = cache['GSMS']
        self.info = cache['info']

    def remove_gsm_cache(self):
        """
        This function will remove individual GSM file caches from the local .cache folder this will leave
        all the information cached and encapsulated as a single GSE cache that contains a list of GSM objects
        :return:
        """
        for gsm in self.GSMS:
            os.remove(CACHE_FOLDER + self.GSMS[gsm].gsm_id + '.ch')

    def __populate_class(self, mode, n_threads=1):
        """
        This function will create instances of the GSM object on  which contains the info and methylation beta values
        of a given GSM id, the function will iterate over all GSM associated with a the GSE id given in the __init__
        and save them into the list attribute self.GSMS

        The function will cache the object at the end of the GSM extraction process as a single GSE object cache
        and remove all single GSM cached files associated to the current GSE.
        :return:
        """
        if mode == 'per_gsm':
            if n_threads == 1:
                gsm_ids = self.GSMS
                self.GSMS = dict()
                # tqdm iterator
                itr = tqdm(gsm_ids, leave=False, position=0)
                for gsm in itr:
                    # extract per GSM information using the "GSM" object
                    self.GSMS[gsm] = GSM(gsm)
                    itr.set_postfix({'Last GSM Extracted': gsm})
            else:
                # pool = ThreadPool(processes=n_threads)
                # gsm_ids = list(chunkify(self.GSMS, n_threads))
                # self.GSMS = dict()
                # def threaded_gsm_creation(gsm_id):
                #     gsm = GSM(gsm_id)
                #     return gsm
                # # tqdm iterator
                # itr = tqdm(gsm_ids, leave=False, position=0)
                # for gsms in itr:
                #     # extract per GSM information using the "GSM" object
                #     async_results = []
                #     for gsm in gsms:
                #         async_result = pool.apply_async(threaded_gsm_creation, (gsm,))
                #         async_results.append(async_result)
                #     for process,gsm in zip(async_results,gsms):
                #         as_result = process.get()
                #         self.GSMS[gsm] = as_result
                #         itr.set_postfix({'Last GSM Extracted': gsm})
                gsm_ids = list(chunkify(self.GSMS, n_threads))
                self.GSMS = dict()
                # tqdm iterator
                itr = tqdm(gsm_ids, leave=False, position=0)
                for gsms in itr:
                    # extract per GSM information using the "GSM" object
                    threads = []
                    for gsm in gsms:
                        thread = Thread(target=self.__thread_extraction, args=(gsm,))
                        thread.daemon = True
                        threads.append(thread)
                    for pos in range(n_threads):
                        threads[pos].start()
                    for pos in range(n_threads):
                        threads[pos].join()
                        itr.set_postfix({'Last GSM Extracted': gsm})
        elif mode == 'supp':
            file_name, selection = self.__prompt_to_select_supp_file()

            # extract all GSM info shells with no DATA
            self.__download_GSM_shells()

            if '.csv.gz' in file_name:
                # csv flow
                self.__download_csv_gz(file_name, selection)
            elif '.tar' in file_name and '.gz' not in file_name:
                self.__download_tar(file_name, selection)

            # remove downloaded file
            os.remove(CACHE_FOLDER+file_name)

        else:
            raise ValueError("Invalid mode passed")

    def __download_GSM_shells(self, n_threads=3):
        """
        This function will iterate over all GSM's associated with a GSE and extract only the information
        about each GSE (without methylation data)
        :return:
        """
        gsm_ids = list(chunkify(self.GSMS, n_threads))
        self.GSMS = dict()
        # tqdm iterator
        itr = tqdm(gsm_ids, leave=False, position=0)
        for gsms in itr:
            # extract per GSM information using the "GSM" object
            threads = []
            for gsm in gsms:
                thread = Thread(target=self.__thread_extraction, args=(gsm, True))
                thread.daemon = True
                threads.append(thread)
            for pos in range(len(threads)):
                threads[pos].start()
            for pos in range(len(threads)):
                threads[pos].join()
            itr.set_postfix({'Last GSM Extracted': '|'.join(gsms)})

    def __download_csv_gz(self, file_name, selection):
        # download the sup file
        wget.download(selection, out=CACHE_FOLDER + file_name, bar=progress_bar)
        # unzip file
        temp_folder_name = str(uuid4()) + '/'
        os.makedirs(CACHE_FOLDER + temp_folder_name)
        # unpack and delete zipped version
        gunzip_shutil(CACHE_FOLDER + file_name, CACHE_FOLDER + temp_folder_name + file_name[:-3])
        # delete zipped file
        os.remove(CACHE_FOLDER + file_name)

        # load data into GSM's objects
        data_ = pd.read_csv(CACHE_FOLDER + temp_folder_name + file_name[:-3], index_col=0)
        for column in data_.columns:
            for gsm in self.GSMS:
                if self.GSMS[gsm].info['title'] == column:
                    # attach data to GSM object from downloaded csv
                    self.GSMS[gsm].data = data_[column]
                    # rename Series title to GSM id
                    self.GSMS[gsm].data.name = gsm
        # remove temp folder
        shutil.rmtree(CACHE_FOLDER + temp_folder_name + '/', )

    def __download_tar(self, file_name, selection):

        if file_name not in os.listdir(CACHE_FOLDER):
            # download the sup file
            wget.download(selection, out=CACHE_FOLDER + file_name, bar=progress_bar)
        # unzip file
        temp_folder_name = str(uuid4()) + '/'
        os.makedirs(CACHE_FOLDER + temp_folder_name)
        unzip_tarfile(CACHE_FOLDER + file_name, CACHE_FOLDER + temp_folder_name)

        # remove all files that dont have .idat in them
        remove_non_idat_files(CACHE_FOLDER + temp_folder_name)
        # validate folder is not empty of idat files
        if len(os.listdir(CACHE_FOLDER + temp_folder_name)) == 0:
            raise Exception('No Idat Files were found in downloaded file!')

        # parse idat values
        parse_idat_files(CACHE_FOLDER + temp_folder_name, ARRAY_TYPES[self.info.loc['platform_id']])
        data_ = pd.read_parquet(CACHE_FOLDER + temp_folder_name + '/parsed_beta_values.parquet')
        # attach data to GSM objects
        for column in data_.columns:
            # attach data to GSM object from downloaded csv
            self.GSMS[column].data = data_[column]
            self.GSMS[column].data.name = column

         # remove temp folder
        shutil.rmtree(CACHE_FOLDER + temp_folder_name + '/')

    def __prompt_to_select_supp_file(self):
        supp_files = self.info['supplementary_file'] if type(self.info['supplementary_file']) != str \
            else [self.info['supplementary_file']]
        print('Please Select a Supplementary File to Download and Parse:\n')
        for ax, file in enumerate(supp_files):
            print(f'{ax + 1}. {file.split("/")[-1]}')
        selection = int(input('-> '))
        file_name = supp_files[selection - 1].split("/")[-1]
        print('Selected: ', file_name)
        return file_name, supp_files[selection - 1]

    def __thread_extraction(self, gsm_id, shell_only=False):
        exhaust = 0
        while exhaust < 4:
            try:
                if shell_only:
                    gsm = GSM(gsm_id, shell_only=True)
                    self.GSMS[gsm_id] = gsm
                    break
                else:
                    gsm = GSM(gsm_id)
                    self.GSMS[gsm_id] = gsm
                    break
            except Exception as e:
                if str(e) == 'No Data Available on GSM card':
                    self.no_data_GSMS.append(gsm_id)
                    break
                else:
                    # pop bad object if in dict
                    if gsm_id in self.GSMS:
                        self.GSMS.pop(gsm_id)
                    related_files = [file for file in os.listdir(CACHE_FOLDER) if gsm_id in file]
                    for file in related_files:
                        # remove bad downloaded files
                        os.remove(CACHE_FOLDER + file)
                        exhaust += 1
                    # re try download

    def __extract_info(self):
        """
        This method will extract the info from an NCBI GSE card and set it as the "info" attribute
        :return:
        """
        # send query requests to NCBI server
        response = get_data_locally(f'{NCBI_QUERY_URL}{self.gse_id}&targ=self&form=text&view=quick')
        # Extract gsm ids from gse soft file for later extraction
        self.GSMS = gsms_from_gse_soft(response)
        # create an info pandas Series object excluding the GSM ids
        self.info = parse_and_compress_gse_info(response)

    def to_dataframe(self, section):
        """
        This function accepts a single parameter "section", depending on the parameter value the function will
        return a pandas dataframe with one of two contents.
        if section == "info" then the returned dataframe will contain the information from all the individual GSMS
        as a single object where the indecies of this dataframe are the information key name and the columns are
        named after the GSMS, i.e there will be N columns where N is the number of GSM's in the corresponding GSE
        :param section: str, one of two values info/data.

        if section == 'data' then the function will return a dataframe that contains the methylation value for all
        relevant cpg cites for this GSE.
        the returned data frame will have K indices where the length of K is equal to the number of cpg cites in the
        GSE's array type, and the values of K are the cpg id's eg. (cpg0000027)
        :return:
        """

        if section == 'info':
            # get all info parameters from each GSM object
            all_infos = [self.GSMS[gsm].info for gsm in self.GSMS]
            # returned concatenated dataframe
            return pd.concat(all_infos, axis=1)
        elif section == 'data':
            # get all methylation data from each GSM object
            all_infos = [self.GSMS[gsm].data[self.GSMS[gsm].gsm_id] for gsm in self.GSMS]
            # returned concatenated dataframe
            return pd.concat(all_infos, axis=1)
        else:
            raise ValueError('Bad section typed given, please user info / data as your section parameter')

    def __len__(self):
        return len(self.GSMS)

    def __getitem__(self, gsm_id):
        if gsm_id in self.GSMS:
            return self.GSMS[gsm_id]
        else:
            raise IndexError(f'{gsm_id} is not in {self.gse_id}')

    def __repr__(self):
        str_fmt = "GSE: {}\nArray Type: {} ({})\nNumber of Samples: {}\nTitle: {}\n".format( \
            colored(self.gse_id, color='green', attrs=['bold']),
            colored(self.info['platform_id'], color='green', attrs=['bold']),
            colored(ARRAY_TYPES[self.info['platform_id']], color='green', attrs=['bold']),
            colored(len(self.GSMS), color='green', attrs=['bold']),
            colored(self.info['title'], color='green', attrs=['bold']))
        print(str_fmt)

    def __str__(self):
        str_fmt = "GSE: {}\nArray Type: {} ({})\nNumber of Samples: {}\nTitle: {}\n".format( \
            self.gse_id, self.info['platform_id'],
            ARRAY_TYPES[self.info['platform_id']],
            len(self.GSMS),
            self.info['title'])
        return str_fmt

    def __eq__(self, other):
        return True if hash(str(self)) == hash(str(other)) else False
