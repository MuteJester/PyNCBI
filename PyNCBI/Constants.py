from pathlib import Path

NCBI_QUERY_BASE_URL = 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi'
NCBI_QUERY_URL = NCBI_QUERY_BASE_URL + '?acc='
SAMPLE_SHEET_COLUMNS = ['Sample_Name', 'Sample_Plate', 'Sample_Group',
                        'Pool_ID', 'Project', 'Sample_Well', 'Sentrix_ID', 'Sentrix_Position']
LOCAL_DOWNLOADS_FOLDER = str(Path.home()) + '/Downloads/'

CACHE_FOLDER = str(Path.home()) + '/.cache/PyNCBI/'

ARRAY_TYPES = {
    'GPL13534':'450k',
    'GPL16304':'450k',
    'GPL21145':'epic',
    'GPL23976':'epic'
}