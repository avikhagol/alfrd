# ALFRD : Automated Logical FRamework for Dynamic script execution(ALFRD)

This program is written for the [SMILE project](https://smilescience.info) supported by the ERC starting grant, particularly with following purposes in mind:

- Communicate with google spreadsheets(or local csv file) and update progress periodically when required.
- Execute a pipeline workflow that dynamically operates based on the progress and requirements recorded in the spreadsheet or CSV file.

# Contents:
- [ALFRD : Automated Logical FRamework for Dynamic script execution(ALFRD)](#alfrd--automated-logical-framework-for-dynamic-script-executionalfrd)
- [Contents:](#contents)
  - [1. Create API credentials on Google Cloud](#1-create-api-credentials-on-google-cloud)
  - [2. Installing](#2-installing)
  - [3. Using ALFRD](#3-using-alfrd)
    - [3.1 - Basic Usage](#31---basic-usage)
      - [3.1.1 Example: Google Spreadsheet - Initializing and creating instance](#311-example-google-spreadsheet---initializing-and-creating-instance)
      - [3.1.2 Example: Google Spreadsheet - Update the data](#312-example-google-spreadsheet---update-the-data)
      - [3.1.3 Example: CSV - Update the data (more soon)](#313-example-csv---update-the-data-more-soon)
    - [3.2 - Advance](#32---advance)
      - [3.2.1 Example : Execute functions using alfrd](#321-example--execute-functions-using-alfrd)
      - [3.2.2 Example : Pipeline step execution/update using the Spreadsheet/CSV](#322-example--pipeline-step-executionupdate-using-the-spreadsheetcsv)
  - [4. Attribution](#4-attribution)
  - [5. Acknowledgement](#5-acknowledgement)


-------


## 1. Create API credentials on Google Cloud


A similar guide is available [here](https://developers.google.com/workspace/guides/create-credentials) or [https://developers.google.com/workspace/guides/create-credentials](https://developers.google.com/workspace/guides/create-credentials)

NOTE: In order to successfully create a google console project a billing detail is usually required. But the sheets API service is available for free, refer [here](https://developers.google.com/sheets/api/limits)

- step 1:
  Go to https://console.cloud.google.com/

- step 2: 
	click on the drop-down to create a new project 
		- can choose organization or leave on default

- step 3
  Search in the top bar (or press / ) and type : "Google sheets api"
  
- step 4
  In the search results - under Marketplace select the first result which should be the same : Google sheets api
  
- step 5
  Enable the service In the product details page 
  
- step 6
  Now select create credentials > Application Data
  
- step 7
  Create an account name
  Select create and continue
  
- step 8
  Search and select "Editor" in role > Continue
  
- step 9
  Skip next optional step
  Select Done
  
- step 10
  The Credentials are successfully created
  Select "Credentials" on the left menu
  
- step 11
  select/edit account that was just created
  also copy the email address that is shown
  
- step 12
  Select keys tab > Add keys > Create New Keys > JSON > save
  
- step 13
  Go to the Google spreadsheet and "share" the sheet to the email address that was copied, as Editor.

## 2. Installing

- Install using the pip package manager:
  ```bash
  pip install alfrd
  ```
- Alternatively Download [ALFRD](https://github.com/avialxee/alfrd) and unzip / Or 
    ```bash
   git clone https://github.com/avialxee/alfrd
   cd alfrd/
   pip install .
    ```
this should install alfrd and all the dependencies automatically.


## 3. Using ALFRD

ALFRD can be used for structuring the pipeline/workflow steps, such that each step (e.g., Step A, Step B, Step C) is represented as a column (A, B, C) in a table, with the workflow executing these steps sequentially according to their order in the table.

### 3.1 - Basic Usage

ALFRD relies on pandas dataframe to read/write tabular data.

####  3.1.1 Example: Google Spreadsheet - Initializing and creating instance

```python
from alfrd.lib import GSC, LogFrame
from alfrd.util import timeinmin, read_inputfile

url='https://spreadsheet/link'
worksheet='worksheet-name'

gsc = GSC(url=url, wname=worksheet, key='path/to/json/file')      # default path for key = home/usr/.alfred
df_sheet = gsc.open()
```


####  3.1.2 Example: Google Spreadsheet - Update the data

The instance of LogFrame can be used to manipulate the dataframe and update the Google Sheet.

```python

lf = LogFrame(gsc=gsc)
lf.df_sheet.loc[0, 'TSYS'] = True

lf.update_sheet(count=1, failed=0,csvfile='df_sheet.csv')                     # if updating the sheet fails, a copy of the dataframe is saved locally at the csvfile path.

```

####  3.1.3 Example: CSV - Update the data (more soon)

It is also possible to use just the CSV file as an alternative to the Google Sheet.

```python

lf = LogFrame(csv='in.csv')
lf.df_sheet.loc[0, 'TSYS'] = True
lf.df_sheet.to_csv('out.csv')

```

### 3.2 - Advance

#### 3.2.1 Example : Execute functions using alfrd

Create `pipe.py` and use the register decorator for creating a pipeline step.

```python 
# pipe.py
from alfrd.plugins import register


@register("A hello world function")
def step_hello_world(name):
  print(f"hello, {name}")

```

Now we should create a pipeline project and add the script to the project, this can be done as follows:

```bash
alfrd init PROJECT_NAME # change the PROJECT_NAME to something desirable
alfrd add /path/to/pipe.py PROJECT_NAME
```

this will create a symlink to the project directory found at  `~/.alfrd/projects/PROJECT_NAME/pipe.py`
That's it! You have created a pipeline flow, now execute the created script by running the following:

```bash
alfrd run step_hello_world PROJECT_NAME name=World
```
> NOTE: you can create a config file e.g config.txt and modify the above as follows: \
>   `alfrd run step_hello_world PROJECT_NAME config.txt` \
> and in the config.txt write something like:
```
# config.txt
name = World
```

#### 3.2.2 Example : Pipeline step execution/update using the Spreadsheet/CSV

Now modify the `pipe.py` and use the validator decorator for defining functions which can be executed just before and after the main pipeline step. 
The parameters accessed between the pipeline steps and validators can be defined by the `Pipeline.params`. 
Also one can use the config file to initialize the parameter values in the execution time.

```python 
# pipe.py

from alfrd.lib import GSC, LogFrame

from alfrd import Pipeline
from alfrd.plugins import validator, validate, register

@validator(desc="Update values on the google sheet", run_once=False, after=True)
def update_sheet(lf, success_count, failed_count):
    lf.update_sheet(success_count, failed_count)
  
@validator(desc="Connect with the google sheet and return an instance for the runtime parameter", run_once=True)
def connect_sheet(sheet_url, worksheet):
    gsc = GSC(url=sheet_url, wname=worksheet, key='/path/to/credentials.json')
    gsc.open()
    Pipeline.params['lf'] = LogFrame(gsc=gsc)

@validate(by=[connect_sheet, update_sheet])
@register("A hello world function")
def modify_tsys(lf):
  lf.df_sheet.loc[0, 'TSYS'] = True
  Pipeline.params['success_count'] = 1
  Pipeline.params['failed_count'] = 0

```

the above can be executed as follows:
```bash
 alfrd run modify_tsys PROJECT_NAME sheet_url=/path/to/sheet worksheet=main
```

## 4. Attribution

When using ALFRD, please add a link to this repository in a footnote.

## 5. Acknowledgement

ALFRD was developed within the "Search for Milli-Lenses" (SMILE) project. SMILE has received funding from the European Research Council (ERC) under the HORIZON ERC Grants 2021 programme (grant agreement No. 101040021).