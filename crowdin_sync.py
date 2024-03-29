#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# crowdin_sync.py
#
# Updates Crowdin source translations and pushes translations
# directly to AICP Gerrit.
#
# Copyright (C) 2014-2016 The CyanogenMod Project
# Copyright (C) 2017-2020 The LineageOS Project
# This code has been modified.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# ################################# IMPORTS ################################## #

import argparse
import json
import git
import os
import re
import shutil
import subprocess
import sys
import yaml

from lxml import etree
from signal import signal, SIGINT

# ################################# GLOBALS ################################## #

_DIR = os.path.dirname(os.path.realpath(__file__))
_COMMITS_CREATED = False

# ################################ FUNCTIONS ################################# #


def run_subprocess(cmd, silent=False):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True)
    comm = p.communicate()
    exit_code = p.returncode
    if exit_code != 0 and not silent:
        print("There was an error running the subprocess.\n"
              "cmd: %s\n"
              "exit code: %d\n"
              "stdout: %s\n"
              "stderr: %s" % (cmd, exit_code, comm[0], comm[1]),
              file=sys.stderr)
    return comm, exit_code


def add_target_paths(config_files, repo, base_path, project_path):
    # Add or remove the files given in the config files to the commit
    count = 0
    file_paths = []
    for f in config_files:
        fh = open(f, "r")
        try:
            config = yaml.safe_load(fh)
            for tf in config['files']:
                if project_path in tf['source']:
                    target_path = tf['translation']
                    lang_codes = tf['languages_mapping']['android_code']
                    for l in lang_codes:
                        lpath = get_target_path(tf['translation'], tf['source'],
                            lang_codes[l], project_path)
                        file_paths.append(lpath)
        except yaml.YAMLError as e:
            print(e, '\n Could not parse YAML.')
            exit()
        fh.close()

    # Strip all comments
    for f in file_paths:
        clean_xml_file(base_path, project_path, f)

    # Modified and untracked files
    modified = repo.git.ls_files(m=True, o=True)
    for m in modified.split('\n'):
        if m in file_paths:
            repo.git.add(m)
            count += 1

    deleted = repo.git.ls_files(d=True)
    for d in deleted.split('\n'):
        if d in file_paths:
            repo.git.rm(d)
            count += 1

    return count


def split_path(path):
    # Split the given string to path and filename
    if '/' in path:
        original_file_name = path[1:][path.rfind("/"):]
        original_path = path[:path.rfind("/")]
    else:
        original_file_name = path
        original_path = ''

    return original_path, original_file_name


def get_target_path(pattern, source, lang, project_path):
    # Make strings like '/%original_path%-%android_code%/%original_file_name%' valid file paths
    # based on the source string's path
    original_path, original_file_name = split_path(source)

    target_path = pattern #.lstrip('/')
    target_path = target_path.replace('%original_path%', original_path)
    target_path = target_path.replace('%android_code%', lang)
    target_path = target_path.replace('%original_file_name%', original_file_name)
    target_path = target_path.replace(project_path, '')
    target_path = target_path.lstrip('/')
    return target_path


def clean_xml_file(base_path, project_path, filename):
    path = base_path + '/' + project_path + '/' + filename

    # We don't want to create every file, just work with those already existing
    if not os.path.isfile(path):
        return

    try:
        fh = open(path, 'r+')
    except:
        print(f'\nSomething went wrong while opening file {path}')
        return

    XML = fh.read()
    content = ''

    # Take the original xml declaration and prepend it
    declaration = XML.split('\n')[0]
    if '<?' in declaration:
        content = declaration + '\n'
        XML = XML[XML.find('\n')+1:]

    try:
        tree = etree.fromstring(XML)
    except etree.XMLSyntaxError as err:
        print(f'{filename}: XML Error: {err.error_log}')
        filename, ext = os.path.splitext(path)
        if ext == '.xml':
            reset_file(path, repo)
        return

    # Remove strings with 'product=*' attribute but no 'product=default'
    # This will ensure aapt2 will not throw an error when building these
    productStrings = tree.xpath("//string[@product]")
    alreadyRemoved = []
    for ps in productStrings:
        # if we already removed the items, don't process them
        if ps in alreadyRemoved:
            continue
        stringName = ps.get('name')
        stringsWithSameName = tree.xpath("//string[@name='{0}']".format(stringName))

        # We want to find strings with product='default' or no product attribute at all
        hasProductDefault = False
        for string in stringsWithSameName:
            product = string.get('product')
            if product is None or product == 'default':
                hasProductDefault = True
                break

        # Every occurance of the string has to be removed when no string with the same name and
        # 'product=default' (or no product attribute) was found
        if not hasProductDefault:
            print(f"\n{path}: Found string '{stringName}' with missing 'product=default' attribute",
                  end='')
            for string in stringsWithSameName:
                tree.remove(string)
                alreadyRemoved.append(string)

    header = ''
    comments = tree.xpath('//comment()')
    for c in comments:
        p = c.getparent()
        if p is None:
            # Keep all comments in header
            header += str(c).replace('\\n', '\n').replace('\\t', '\t') + '\n'
            continue
        p.remove(c)

    # Take the original xml declaration and prepend it
    declaration = XML.split('\n')[0]
    if '<?' in declaration:
        content = declaration + '\n'

    content += etree.tostring(tree, pretty_print=True, encoding="unicode", xml_declaration=False)

    if header != '':
        content = content.replace('?>\n', '?>\n' + header)

    # Sometimes spaces are added, we don't want them
    content = re.sub("[ ]*<\/resources>", "</resources>", content)

    # Overwrite file with content stripped by all comments
    fh.seek(0)
    fh.write(content)
    fh.truncate()
    fh.close()

    # Remove files which don't have any translated strings
    contentList = list(tree)
    if len(contentList) == 0:
        print(f'\nRemoving {path}')
        os.remove(path)


# For files we can't process due to errors, create a backup
# and checkout the file to get it back to the previous state
def reset_file(filepath, repo):
    backupFile = None
    parts = filepath.split("/")
    found = False
    for s in parts:
        curPart = s
        if not found and s.startswith("res"):
            curPart = s + "_backup"
            found = True
        if backupFile is None:
            backupFile = curPart
        else:
            backupFile = backupFile + '/' + curPart

    path, filename = os.path.split(backupFile)
    if not os.path.exists(path):
        os.makedirs(path)
    if os.path.exists(backupFile):
        i = 1
        while os.path.exists(backupFile + str(i)):
            i+=1
        backupFile = backupFile + str(i)
    shutil.copy(filepath, backupFile)
    repo.git.checkout(filepath)


def push_as_commit(config_files, base_path, path, name, branch, username):
    global _COMMITS_CREATED
    print(f'\nCommitting {name} on branch {branch}: ', end='')

    # Get path
    project_path = path
    path = os.path.join(base_path, path)
    if not path.endswith('.git'):
        path = os.path.join(path, '.git')

    # Create repo object
    repo = git.Repo(path)

    # Add all files to commit
    count = add_target_paths(config_files, repo, base_path, project_path)

    if count == 0:
        print('Nothing to commit')
        return

    # Create commit; if it fails, probably empty so skipping
    try:
        repo.git.commit(m='Automatic AICP translation import')
    except:
        print('Failed, probably empty: skipping', file=sys.stderr)
        return

    # Push commit
    try:
        repo.git.push(f'ssh://{username}@gerrit.aicp-rom.com:29418/{name}',
                      f'HEAD:refs/for/{branch}', '-o', f'topic=Translations-{branch}')
        print('Success')
    except Exception as e:
        print(e, '\nFailed to push!', file=sys.stderr)
        return

    _COMMITS_CREATED = True


def submit_gerrit(branch, username, owner):
    # If an owner is specified, modify the query so we only get the ones wanted
    ownerArg = ''
    if owner is not None:
        ownerArg = f'owner:{owner}'

    # Find all open translation changes
    cmd = ['ssh', '-p', '29418',
        f'{username}@gerrit.aicp-rom.com',
        'gerrit', 'query',
        'status:open',
        f'branch:{branch}',
        ownerArg,
        'message:"Automatic AICP translation import"',
        f'topic:Translations-{branch}',
        '--current-patch-set',
        '--format=JSON']
    commits = 0
    msg, code = run_subprocess(cmd)
    if code != 0:
        print(f'Failed: {msg[1]}')
        return

    # Each line is one valid JSON object, except the last one, which is empty
    for line in msg[0].strip('\n').split('\n'):
        js = json.loads(line)
        # We get valid JSON, but not every result line is a line we want
        if not 'currentPatchSet' in js or not 'revision' in js['currentPatchSet']:
            continue
        # Add Code-Review +2 and Verified +1 labels and submit
        cmd = ['ssh', '-p', '29418',
        f'{username}@gerrit.aicp-rom.com',
        'gerrit', 'review',
        '--verified +1',
        '--code-review +2',
        '--submit', js['currentPatchSet']['revision']]
        msg, code = run_subprocess(cmd, True)
        print('Submitting commit %s: ' % js['url'], end='')
        if code != 0:
            errorText = msg[1].replace('\n\n', '; ').replace('\n', '')
            print(f'Failed: {errorText}')
        else:
            print('Success')

        commits += 1

    if commits == 0:
        print("Nothing to submit!")
        return


def check_run(cmd):
    p = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
    ret = p.wait()
    if ret != 0:
        joined = ' '.join(cmd)
        print(f'Failed to run cmd: {joined}', file=sys.stderr)
        sys.exit(ret)


def find_xml(base_path):
    for dp, dn, file_names in os.walk(base_path):
        for f in file_names:
            if os.path.splitext(f)[1] == '.xml':
                yield os.path.join(dp, f)

# ############################################################################ #


def parse_args():
    parser = argparse.ArgumentParser(
        description="Synchronising AICP translations with Crowdin")
    sync = parser.add_mutually_exclusive_group()
    parser.add_argument('--username', help='Gerrit username')
    parser.add_argument('--branch', help='AICP branch', required=True)
    parser.add_argument('-c', '--config', help='Custom yaml config')
    parser.add_argument('--upload-sources', action='store_true',
                        help='Upload sources to AICP Crowdin')
    parser.add_argument('--upload-translations', action='store_true',
                        help='Upload AICP translations to Crowdin')
    parser.add_argument('--download', action='store_true',
                        help='Download AICP translations from Crowdin')
    parser.add_argument('--local-download', action='store_true',
                        help='Locally download AICP translations from Crowdin')
    parser.add_argument('--submit', action='store_true',
                        help='Auto-Merge open AICP translations on Gerrit')
    parser.add_argument('-o', '--owner',
                        help='Specify the owner of the commits to submit')
    return parser.parse_args()

# ################################# PREPARE ################################## #


def check_dependencies():
    # Check if crowdin is installed somewhere
    cmd = ['which', 'crowdin']
    if run_subprocess(cmd, silent=True)[1] != 0:
        print('You have not installed crowdin properly.', file=sys.stderr)
        return False
    return True


def load_xml(x):
    try:
        return etree.parse(x)
    except etree.XMLSyntaxError:
        print(f'Malformed {x}', file=sys.stderr)
        return None
    except Exception:
        print(f'You have no {x}', file=sys.stderr)
        return None


def check_files(files):
    for f in files:
        if not os.path.isfile(f):
            print(f'You have no {f}.', file=sys.stderr)
            return False
    return True

# ################################### MAIN ################################### #


def upload_sources_crowdin(project_id, branch, config):
    if config:
        print('\nUploading sources to Crowdin (custom config)')
        check_run(['crowdin', 'upload', 'sources',
                   f'--project-id={project_id}',
                   f'--branch={branch}',
                   '--auto-update',
                   f'--config={_DIR}/config/{config}'])
    else:
        print('\nUploading sources to Crowdin (AOSP supported languages)')
        check_run(['crowdin', 'upload', 'sources',
                   f'--project-id={project_id}',
                   f'--branch={branch}',
                   '--auto-update',
                   f'--config={_DIR}/config/{branch}.yml'])


def upload_translations_crowdin(project_id, branch, config):
    if config:
        print('\nUploading translations to Crowdin (custom config)')
        check_run(['crowdin', 'upload', 'translations',
                   f'--project-id={project_id}',
                   f'--branch={branch}',
                   '--import-eq-suggestions',
                   '--auto-approve-imported',
                   f'--config={_DIR}/config/{config}'])
    else:
        print('\nUploading translations to Crowdin '
              '(AOSP supported languages)')
        check_run(['crowdin', 'upload', 'translations',
                   f'--project-id={project_id}',
                   f'--branch={branch}',
                   '--import-eq-suggestions',
                   '--auto-approve-imported',
                   f'--config={_DIR}/config/{branch}.yml'])


def local_download(project_id, base_path, branch, xml, config):
    if config:
        print('\nDownloading translations from Crowdin (custom config)')
        check_run(['crowdin', 'download',
                   f'--project-id={project_id}',
                   f'--branch={branch}',
                   '--skip-untranslated-strings',
                   '--export-only-approved',
                   f'--config={_DIR}/config/{config}'])
    else:
        print('\nDownloading translations from Crowdin '
              '(AOSP supported languages)')
        check_run(['crowdin', 'download',
                   f'--project-id={project_id}',
                   f'--branch={branch}',
                   '--skip-untranslated-strings',
                   '--export-only-approved',
                   f'--config={_DIR}/config/{branch}.yml'])

    print('\nRemoving useless empty translation files (AOSP supported languages)')
    empty_contents = {
        '<resources/>',
        '<resources xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2"/>',
        ('<resources xmlns:android='
         '"http://schemas.android.com/apk/res/android"/>'),
        ('<resources xmlns:android="http://schemas.android.com/apk/res/android"'
         ' xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2"/>'),
        ('<resources xmlns:tools="http://schemas.android.com/tools"'
         ' xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2"/>'),
        ('<resources xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2">\n</resources>'),
        ('<resources xmlns:android='
         '"http://schemas.android.com/apk/res/android">\n</resources>'),
        ('<resources xmlns:android="http://schemas.android.com/apk/res/android"'
         ' xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2">\n</resources>'),
        ('<resources xmlns:tools="http://schemas.android.com/tools"'
         ' xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2">\n</resources>'),
        ('<resources>\n</resources>')
    }

    xf = None
    cmd = ['crowdin', 'list', 'translations',
           f'--project-id={project_id}',
           '--plain',
           f'--config={_DIR}/config/{branch}.yml']
    comm, ret = run_subprocess(cmd)
    if ret != 0:
        sys.exit(ret)
    # Split in list and remove last empty entry
    xml_list=str(comm[0]).split("\n")[:-1]
    for xml_file in xml_list:
        try:
            print("Checking: " + base_path + '/' + xml_file)
            tree = etree.XML(open(base_path + '/' + xml_file).read().encode())
            etree.strip_tags(tree,etree.Comment)
            treestring = etree.tostring(tree,encoding='UTF-8')
            xf = "".join([s for s in treestring.decode().strip().splitlines(True) if s.strip()])
            for line in empty_contents:
                if line in xf:
                    print("Removing: " + base_path + '/' + xml_file)
                    os.remove(base_path + '/' + xml_file)
                    break
        except IOError:
            print("File not found: " + xml_file)
            sys.exit(1)
        except etree.XMLSyntaxError:
            print("XML Syntax error in file: " + xml_file)
            sys.exit(1)
    del xf


def download_crowdin(project_id, base_path, branch, xml, username, config):
    local_download(project_id, base_path, branch, xml, config)

    print('\nCreating a list of pushable translations')
    # Get all files that Crowdin pushed
    paths = []
    if config:
        files = [f'{_DIR}/config/{config}']
    else:
        files = [f'{_DIR}/config/{branch}.yml']
    for c in files:
        cmd = ['crowdin', 'list', 'sources',
               f'--branch={branch}', f'--config={c}', '--plain']
        comm, ret = run_subprocess(cmd)
        if ret != 0:
            sys.exit(ret)
        for p in str(comm[0]).split("\n"):
            paths.append(p.replace(f'/{branch}', ''))

    print('\nUploading translations to AICP Gerrit')
    items = [x for xmlfile in xml for x in xmlfile.findall("//project")]
    all_projects = []

    for path in paths:
        path = path.strip()
        if not path:
            continue

        if "/res" not in path:
            print(f'WARNING: Cannot determine project root dir of [{path}], skipping.')
            continue

        # Usually the project root is everything before /res
        # but there are special cases where /res is part of the repo name as well
        parts = path.split("/res")
        if len(parts) == 2:
            result = parts[0]
        elif len(parts) == 3:
            result = parts[0] + '/res' + parts[1]
        else:
            print(f'WARNING: Splitting the path not successful for [{path}], skipping')
            continue

        result = result.strip('/')
        if result == path.strip('/'):
            print(f'WARNING: Cannot determine project root dir of [{path}], skipping.')
            continue

        if result in all_projects:
            continue

        # When a project has multiple translatable files, Crowdin will
        # give duplicates.
        # We don't want that (useless empty commits), so we save each
        # project in all_projects and check if it's already in there.
        all_projects.append(result)

        # Search AICP/platform_manifest/*.xml or
        # config/%(branch)_extra_packages.xml for the project's name
        resultPath = None
        resultProject = None
        for project in items:
            path = project.get('path')
            if not (result + '/').startswith(path +'/'):
                continue
            # We want the longest match, so projects in subfolders of other projects are also
            # taken into account
            if resultPath is None or len(path) > len(resultPath):
                resultPath = path
                resultProject = project

        # Just in case no project was found
        if resultPath is None:
            continue

        if result != resultPath:
            if resultPath in all_projects:
                continue
            result = resultPath
            all_projects.append(result)

        br = resultProject.get('revision') or branch

        push_as_commit(files, base_path, result,
                       resultProject.get('name'), br, username)


def sig_handler(signal_received, frame):
    print('')
    print('SIGINT or CTRL-C detected. Exiting gracefully')
    exit(0)


def main():
    global _COMMITS_CREATED
    signal(SIGINT, sig_handler)
    args = parse_args()
    default_branch = args.branch

    if args.submit:
        if args.username is None:
            print('Argument -u/--username is required for submitting!')
            sys.exit(1)
        submit_gerrit(default_branch, args.username, args.owner)
        sys.exit(0)

    project_id_env = 'AICP_CROWDIN_PROJECT_ID'
    project_id = os.getenv(project_id_env)

    base_path_branch_suffix = default_branch.replace('.', '_')
    base_path_env = f'AICP_CROWDIN_BASE_PATH_{base_path_branch_suffix}'
    base_path = os.getenv(base_path_env)
    if base_path is None:
        cwd = os.getcwd()
        print(f'You have not set {base_path_env}. Defaulting to {cwd}')
        base_path = cwd
    if not os.path.isdir(base_path):
        print(f'{base_path_env} is not a real directory: {base_path}')
        sys.exit(1)

    if not check_dependencies():
        sys.exit(1)

    xml_default = load_xml(x=f'{base_path}/platform_manifest/crowdin.xml')
    if xml_default is None:
        sys.exit(1)

    xml_extra = load_xml(x=f'{_DIR}/config/{default_branch}_extra_packages.xml')
    if xml_extra is not None:
        xml_files = (xml_default, xml_extra)
    else:
        xml_files = (xml_default)

    if args.config:
        files = [f'{_DIR}/config/{args.config}']
    else:
        files = [f'{_DIR}/config/{default_branch}.yml']
    if not check_files(files):
        sys.exit(1)

    if args.download and args.username is None:
        print('Argument --username is required to perform this action')
        sys.exit(1)

    if args.upload_sources:
        upload_sources_crowdin(project_id, default_branch, args.config)

    if args.upload_translations:
        upload_translations_crowdin(project_id, default_branch, args.config)

    if args.local_download:
        local_download(project_id, base_path, default_branch, xml_files, args.config)

    if args.download:
        download_crowdin(project_id, base_path, default_branch, xml_files, args.username, args.config)

    if _COMMITS_CREATED:
        print('\nDone!')
        sys.exit(0)
    else:
        print('\nFinished! Nothing to do or commit anymore.')
        sys.exit(2)

if __name__ == '__main__':
    main()
