#!/usr/bin/env python
# -*- coding: utf-8 -*-
# crowdin-cli_sync.py
#
# Updates Crowdin source translations and pushes translations
# directly to AICP Gerrit.
#
# Copyright (C) 2014-2016 The CyanogenMod Project
# Copyright (C) 2017-2018 The LineageOS Project
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

from __future__ import print_function

import argparse
import git
import os
import subprocess
import sys

from xml.dom import minidom
from lxml import etree

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


def push_as_commit(base_path, path, name, branch, username):
    print('Committing %s on branch %s' % (name, branch))

    # Get path
    path = os.path.join(base_path, path)
    if not path.endswith('.git'):
        path = os.path.join(path, '.git')

    # Create repo object
    repo = git.Repo(path)

    # Remove previously deleted files from Git
    files = repo.git.ls_files(d=True).split('\n')
    if files and files[0]:
        repo.git.rm(files)

    # Add all files to commit
    repo.git.add('-A')

    # Create commit; if it fails, probably empty so skipping
    message = 'Automatic AICP translation import'

    try:
        repo.git.commit(m=message)
    except:
        print('Failed to create commit for %s, probably empty: skipping'
              % name, file=sys.stderr)
        return

    # Push commit
    try:
        repo.git.push('ssh://%s@gerrit.aicp-rom.com:29418/%s' % (username, name),
                      'HEAD:refs/for/%s%%topic=Translations-%s' % (branch, branch))
        print('Successfully pushed commit for %s' % name)
    except:
        print('Failed to push commit for %s' % name, file=sys.stderr)

    _COMMITS_CREATED = True


def submit_gerrit(branch, username):
    # Find all open translation changes
    cmd = ['ssh', '-p', '29418',
        '{}@gerrit.aicp-rom.com'.format(username),
        'gerrit', 'query',
        'status:open',
        'branch:{}'.format(branch),
        'message:"Automatic AICP translation import"',
        'topic:Translations-{}'.format(branch),
        '--current-patch-set']
    commits = []
    msg, code = run_subprocess(cmd)
    if code != 0:
        print('Failed: {0}'.format(msg[1]))
        return

    for line in msg[0].split('\n'):
        if "revision:" not in line:
            continue;
        elements = line.split(': ');
        if len(elements) != 2:
            print('Unexpected line found: {0}'.format(line))
        commits.append(elements[1])

    if len(commits) == 0:
        print("Nothing to submit!")
        return

    for commit in commits:
        # Add Code-Review +2 and Verified +1 labels and submit
        cmd = ['ssh', '-p', '29418',
        '{}@gerrit.aicp-rom.com'.format(username),
        'gerrit', 'review',
        '--verified +1',
        '--code-review +2',
        '--submit', commit]
        msg, code = run_subprocess(cmd, True)
        if code != 0:
            errorText = msg[1].replace('\n\n', '; ').replace('\n', '')
            print('Error on submitting commit {0}: {1}'.format(commit, errorText))
        else:
            print('Success on submitting commit {0}'.format(commit))


def check_run(cmd):
    p = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
    ret = p.wait()
    if ret != 0:
        print('Failed to run cmd: %s' % ' '.join(cmd), file=sys.stderr)
        sys.exit(ret)

# ############################################################################ #


def parse_args():
    parser = argparse.ArgumentParser(
        description="Synchronising AICP translations with Crowdin")
    parser.add_argument('--username', help='Gerrit username',
                        required=True)
    parser.add_argument('--branch', help='AICP branch',
                        required=True)
    parser.add_argument('-c', '--config', help='Custom yaml config')
    parser.add_argument('--upload-sources', action='store_true',
                        help='Upload sources to AICP Crowdin')
    parser.add_argument('--upload-translations', action='store_true',
                        help='Upload AICP translations to Crowdin')
    parser.add_argument('--download', action='store_true',
                        help='Download AICP translations from Crowdin')
    parser.add_argument('--local-download', action='store_true',
                        help='local Download AICP translations from Crowdin')
    parser.add_argument('--submit', action='store_true',
                        help='Auto-Merge open AICP translations on Gerrit')
    return parser.parse_args()

# ################################# PREPARE ################################## #


def check_dependencies():
    # Check for Java version of crowdin-cli
    cmd = ['find', '/usr/local/bin/crowdin-cli.jar']
    if run_subprocess(cmd, silent=True)[1] != 0:
        print('You have not installed crowdin-cli.jar in its default location.', file=sys.stderr)
        return False
    return True


def load_xml(x):
    try:
        return minidom.parse(x)
    except IOError:
        print('You have no %s.' % x, file=sys.stderr)
        return None
    except Exception:
        print('Malformed %s.' % x, file=sys.stderr)
        return None


def check_files(files):
    for f in files:
        if not os.path.isfile(f):
            print('You have no %s.' % f, file=sys.stderr)
            return False
    return True

# ################################### MAIN ################################### #


def upload_sources_crowdin(branch, config):
    if config:
        print('\nUploading sources to Crowdin (custom config)')
        check_run(['java', '-jar', '/usr/local/bin/crowdin-cli.jar',
                   '--config=%s/config/%s' % (_DIR, config),
                   'upload', 'sources', '--branch=%s' % branch])
    else:
        print('\nUploading sources to Crowdin (AOSP supported languages)')
        check_run(['java', '-jar', '/usr/local/bin/crowdin-cli.jar',
                   '--config=%s/config/%s.yaml' % (_DIR, branch),
                   'upload', 'sources', '--branch=%s' % branch])


def upload_translations_crowdin(branch, config):
    if config:
        print('\nUploading translations to Crowdin (custom config)')
        check_run(['java', '-jar', '/usr/local/bin/crowdin-cli.jar',
                   '--config=%s/config/%s' % (_DIR, config),
                   'upload', 'translations', '--branch=%s' % branch,
                   '--no-import-duplicates', '--import-eq-suggestions',
                   '--auto-approve-imported'])
    else:
        print('\nUploading translations to Crowdin '
              '(AOSP supported languages)')
        check_run(['java', '-jar', '/usr/local/bin/crowdin-cli.jar',
                   '--config=%s/config/%s.yaml' % (_DIR, branch),
                   'upload', 'translations', '--branch=%s' % branch,
                   '--no-import-duplicates', '--import-eq-suggestions',
                   '--auto-approve-imported'])


def local_download(base_path, branch, xml, config):
    if config:
        print('\nDownloading translations from Crowdin (custom config)')
        check_run(['java', '-jar', '/usr/local/bin/crowdin-cli.jar',
                   '--config=%s/config/%s' % (_DIR, config),
                   'download', '--branch=%s' % branch])
    else:
        print('\nDownloading translations from Crowdin '
              '(AOSP supported languages)')
        check_run(['java', '-jar', '/usr/local/bin/crowdin-cli.jar',
                   '--config=%s/config/%s.yaml' % (_DIR, branch),
                   'download', '--branch=%s' % branch])

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
        ('<resources xmlns:android="http://schemas.android.com/apk/res/android">\n</resources>'),
        ('<resources xmlns:android="http://schemas.android.com/apk/res/android"'
         ' xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2">\n</resources>'),
        ('<resources xmlns:tools="http://schemas.android.com/tools"'
         ' xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2">\n</resources>'),
        ('<resources>\n</resources>')
}

    xf = None
    dom1 = None
    cmd = ['java', '-jar', '/usr/local/bin/crowdin-cli.jar',
           '--config=%s/config/%s.yaml' % (_DIR, branch), 'list', 'translations']
    comm, ret = run_subprocess(cmd)
    if ret != 0:
        sys.exit(ret)
    # Split in list and remove last empty entry
    xml_list=str(comm[0]).split("\n")[:-1]
    for xml_file in xml_list:
        try:
            tree = etree.fromstring(open(base_path + xml_file).read())
            etree.strip_tags(tree,etree.Comment)
            treestring = etree.tostring(tree)
            xf = "".join([s for s in treestring.strip().splitlines(True) if s.strip()])
            for line in empty_contents:
                if line in xf:
                    print('Removing ' + base_path + xml_file)
                    os.remove(base_path + xml_file)
                    break
        except IOError:
            print("File not found: " + xml_file)
            sys.exit(1)
        except etree.XMLSyntaxError:
            print("XML Syntax error in file: " + xml_file)
            sys.exit(1)
    del xf
    del dom1


def download_crowdin(base_path, branch, xml, username, config):
    local_download(base_path, branch, xml, config)

    print('\nCreating a list of pushable translations')
    # Get all files that Crowdin pushed
    paths = []
    if config:
        files = [('%s/config/%s' % (_DIR, config))]
    else:
        files = [('%s/config/%s.yaml' % (_DIR, branch))]
    for c in files:
        cmd = ['java', '-jar', '/usr/local/bin/crowdin-cli.jar',
               '--config=%s' % c, 'list', 'project', '--branch=%s' % branch]
        comm, ret = run_subprocess(cmd)
        if ret != 0:
            sys.exit(ret)
        for p in str(comm[0]).split("\n"):
            paths.append(p.replace('/%s' % branch, ''))

    print('\nUploading translations to AICP Gerrit')
    args = parse_args()
    default_branch = args.branch
    xml_pm = load_xml(x='%s/platform_manifest/default.xml' % (base_path))
    xml_extra = load_xml(x='%s/config/%s_extra_packages.xml' % (_DIR, default_branch))
    xml_aicp = load_xml(x='%s/platform_manifest/aicp_default.xml' % (base_path))
    items = [x for sub in xml for x in sub.getElementsByTagName('project')]
    all_projects = []

    for path in paths:
        path = path.strip()
        if not path:
            continue

        if "/res" not in path:
            print('WARNING: Cannot determine project root dir of '
                  '[%s], skipping.' % path)
            continue
        result = path.split('/res')[0].strip('/')
        if result == path.strip('/'):
            print('WARNING: Cannot determine project root dir of '
                  '[%s], skipping.' % path)
            continue

        if result in all_projects:
            continue

        # When a project has multiple translatable files, Crowdin will
        # give duplicates.
        # We don't want that (useless empty commits), so we save each
        # project in all_projects and check if it's already in there.
        all_projects.append(result)

        # Search %(branch)/platform_manifest/default.xml or
        # config/%(branch)_extra_packages.xml for the project's name
        for project in items:
            path = project.attributes['path'].value
            if not (result + '/').startswith(path +'/'):
                continue
            if result != path:
                if path in all_projects:
                    break
                result = path
                all_projects.append(result)

            br = project.getAttribute('revision') or branch

            push_as_commit(base_path, result,
                           project.getAttribute('name'), br, username)
            break


def main():
    args = parse_args()
    default_branch = args.branch

    if args.submit:
        if args.username is None:
            print('Argument -u/--username is required for submitting!')
            sys.exit(1)
        submit_gerrit(default_branch, args.username)
        sys.exit(0)

    base_path_branch_suffix = default_branch.replace('.', '_')
    base_path_env = 'AICP_CROWDIN_BASE_PATH_%s' % base_path_branch_suffix
    base_path = os.getenv(base_path_env)
    if base_path is None:
        cwd = os.getcwd()
        print('You have not set %s. Defaulting to %s' % (base_path_env, cwd))
        base_path = cwd
    if not os.path.isdir(base_path):
        print('%s is not a real directory: %s' % (base_path_env, base_path))
        sys.exit(1)

    if not check_dependencies():
        sys.exit(1)

    xml_pm = load_xml(x='%s/platform_manifest/default.xml' % base_path)
    if xml_pm is None:
        sys.exit(1)

    xml_extra = load_xml(x='%s/config/%s_extra_packages.xml' % (_DIR, default_branch))
    if xml_extra is None:
        sys.exit(1)

    xml_aicp = load_xml(x='%s/platform_manifest/aicp_default.xml' % base_path)
    if xml_aicp is not None:
        xml_files = (xml_pm, xml_aicp, xml_extra)
    else:
        xml_files = (xml_pm, xml_extra)

    if args.config:
        files = [('%s/config/%s' % (_DIR, args.config))]
    else:
        files = [('%s/config/%s.yaml' % (_DIR, default_branch))]
    if not check_files(files):
        sys.exit(1)

    if args.download and args.username is None:
        print('Argument -u/--username is required to perform this action')
        sys.exit(1)

    if args.upload_sources:
        upload_sources_crowdin(default_branch, args.config)

    if args.upload_translations:
        upload_translations_crowdin(default_branch, args.config)

    if args.download:
        download_crowdin(base_path, default_branch, xml_files,
                         args.username, args.config)

    if args.local_download:
        local_download(base_path, default_branch, xml_files, args.config)


    if _COMMITS_CREATED:
        print('\nDone!')
        sys.exit(0)
    else:
        print('\nFinished! Nothing to do or commit anymore.')
        sys.exit(-1)

if __name__ == '__main__':
    main()
