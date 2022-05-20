import methylprep
import pandas as pd
import os
from tqdm.auto import tqdm
from PyNCBI.Constants import SAMPLE_SHEET_COLUMNS


def check_for_sample_sheet(path):
    """
    This function checks that the given path contains a csv file named "sample_sheet" and that
    it has all needed columns i.e: Sample_Name,Sample_Plate,Sample_Group,Pool_ID,Project,Sample_Well,
    Sentrix_ID,Sentrix_Position

    Note that depending on the situation not all of the columns have to have values,
    the two columns that are an absolute must for other functionality to work in any case are
    Sentrix_ID and Sentrix_Position.
    :param path:
    :return:
    """
    file_in_paths = os.listdir(path)

    if 'sample_sheet.csv' not in file_in_paths:
        return False
    else:
        sample_sheet = pd.read_csv(path+'sample_sheet.csv')
        conditions = [
            (set(sample_sheet.columns)&set(SAMPLE_SHEET_COLUMNS))==set(SAMPLE_SHEET_COLUMNS),
            'Sentrix_ID' in sample_sheet.columns and'Sentrix_Position' in sample_sheet.columns
        ]
        if any(conditions):
            return True
        else:
            return False


def generate_sample_sheet(path):
    """
    This function will iterate over the files in the folder stated in the path variable and generate a sample sheet
    to the sample folder, the folder should only contain red/green idat files

    KEEP IN MIND!
    If the structure of the idat file names is {GSM_NUMBER}_{SENTRIX_ID}_{SENTRIX_POS}_{Grn/Red].idat
    after saving the GSM id to the sample sheet the file will be renamed to {SENTRIX_ID}_{SENTRIX_POS}_{Grn/Red].idat
    to refer again at GSM ids you can check out the sample sheet.
    Also after extracting idat values, the beta value columns are named by GSM for you convince.
    :param path:
    :return:
    """
    sample_sheet = pd.DataFrame(columns=SAMPLE_SHEET_COLUMNS)
    files_in_path = os.listdir(path)
    # TODO: Add folder validation - i.e all files in the folder are red/grn files and that they are coupled

    # remove _red/grn.dat from files suffix
    files_in_path = [i[:-9] for i in files_in_path]
    # remove duplicated base names resulting from above step
    files_in_path = list(set(files_in_path))

    for file in files_in_path:
        components = file.split('_')
        if len(components) == 2:
            sentrix_id, sentrix_position = file.split('_')
            new_row = {key: '' for key in SAMPLE_SHEET_COLUMNS}
            new_row['Sentrix_ID'] = sentrix_id
            new_row['Sentrix_Position'] = sentrix_position
            sample_sheet = sample_sheet.append(new_row, ignore_index=True)
        elif len(components) == 3:
            gsm, sentrix_id, sentrix_position = file.split('_')
            new_row = {key: '' for key in SAMPLE_SHEET_COLUMNS}
            new_row['Sample_Name'] = gsm
            new_row['Sentrix_ID'] = sentrix_id
            new_row['Sentrix_Position'] = sentrix_position
            sample_sheet = sample_sheet.append(new_row, ignore_index=True)
            # rename file
            os.rename(path+file+'_Grn.idat',path+sentrix_id+'_'+sentrix_position+'_Grn.idat')
            os.rename(path+file+'_Red.idat',path+sentrix_id+'_'+sentrix_position+'_Red.idat')

        else:
            raise ValueError('Bad Number of components in files names')

    sample_sheet.to_csv(path+'/sample_sheet.csv',index=False)

def parse_idat_files(path,array_type):
    """
    This function uses methylprep to parse idat files and save the data from each idat file into a separate folder
    in the same path as in the *path* variable.

    If a sample sheet is not present in the folder a new one will be generated

    Also a separate file will be created in the folder called "parsed_beta_values.parquet" that will contain
    N columns where N is equal to the number of samples and K rows where K is equal to the array size - probes that
    didn't pass the QC.

    :param path: path to folder with idat files and sample_sheet
    :param array_type: custom, 450k, epic, and epic+.
    :return:
    """

    # check for sample sheet
    if not check_for_sample_sheet(path):
        print('No Sample Sheet found, Generating Sample Sheet')
        # generate sample sheet
        generate_sample_sheet(path)
    # read sample sheet
    sample_sheet = pd.read_csv(path+'sample_sheet.csv')

    # map GSM to id and position if there are sample names in sample sheet
    sample_name_map = dict()
    if 'Sample_Name' in sample_sheet.columns:
        for ax,row in sample_sheet.iterrows():
            if row['Sample_Name'] != None:
                sample_name_map[str(row['Sentrix_ID'])+'_'+row['Sentrix_Position']] = row['Sample_Name']
            else:
                sample_name_map[str(row['Sentrix_ID'])+'_'+row['Sentrix_Position']] =\
                    str(row['Sentrix_ID'])+'_'+row['Sentrix_Position']

    # validate file names
    # rename files if needed
    # aggregate all sentrix_ids


    dataframe = methylprep.run_pipeline(path,
                                              array_type=array_type, export=False,betas=True,
                            save_control=False,meta_data_frame=False)

    dataframe.columns = [sample_name_map[i] for i in dataframe.columns]
    dataframe.to_parquet(path+'parsed_beta_values.parquet')

    print('All samples parsed and saved')

def merge_gsm_data_files(path):
    """
    This function will aggregate all csv files in a given path that contain the methylation data of a single GSM into
    one large parquet file made out of N columns where N is equal to the number of original separate GSM methylation
    data csv files.

    The structure of the individual GSM csv files MUST have 2 column, a column named "probe" that contains the list
    of cgs and a column named GSMXXXXXX which is the GSM identifier, under this column are the parried values
    corresponding to the ones in the "probe" column.
    :param path:
    :return: This function will save a parquet file in the path folder named "merged_gsms.parquet"
    """

    # only csv files
    files_in_path = [file for file in os.listdir(path) if '.csv' in file]
    individual_gsm_data = []
    for file in tqdm(files_in_path):
        gsm_data = pd.read_csv(path+file)
        data_column = list(filter(lambda x: 'GSM' in x,gsm_data.columns))
        # validate there was indeed a 'GSM' column found
        if len(data_column) != 1 or 'probe' not in gsm_data.columns:
            raise ValueError(f'Error in finding GSM/probe columns when reading {file}')
        data_column = data_column[0]
        individual_gsm_data.append(gsm_data[['probe',data_column]].set_index('probe'))

    merged = pd.concat(individual_gsm_data,axis=1)
    merged.to_parquet(path+'merged_gsms.parquet')
    # Log
    print('merged_gsms.parquet Was successfully saved!')

