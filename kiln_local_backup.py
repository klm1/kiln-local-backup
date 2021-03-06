#!/usr/bin/env python
# encoding: utf-8
"""
kiln_local_backup.py

Copyright (c) 2010-2016 Nate Silva, Ken Morse

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation files
(the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge,
publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
__version__ = "0.4.0"

import sys
import os
import urllib2
import time
import urllib
import urlparse
from operator import itemgetter
from subprocess import Popen, PIPE
from optparse import OptionParser, IndentedHelpFormatter

try:
    import json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        sys.exit('For versions of Python earlier than 2.6, you must ' +
            'install simplejson (http://pypi.python.org/pypi/simplejson/).')

CONFIG_FILE = 'backup.config'
debug = False


def parse_command_line(args):
    """
    Parse the command line arguments.

    Returns a tuple of (options, destination_dir).

    Calls sys.exit() if the command line could not be parsed.
    """

    global debug

    usage = 'usage: %prog [options] DESTINATION-DIR'
    description = 'Backs up all available Mercurial and Git repositories on Kiln ' + \
        'by cloning them (if they have not been backed up before), or by pulling ' + \
        'changes. In order to run this without user interaction, you must install ' + \
        'the FogBugz "KilnAuth" Mercurial extension and appropriate Git extension ' + \
        'then clone at least one repository so that your credentials are saved.'
    version = "%prog, v" + __version__

    parser = OptionParser(usage=usage, description=description, version=version)
    parser.formatter = IndentedHelpFormatter(max_help_position=30)

    parser.add_option('-t', '--token', dest='token', help='FogBugz API token')
    parser.add_option('-s', '--server', dest='server', help='Kiln server name, e.g.: ' + \
        'https://name.kilnhg.com')
    parser.add_option('--ssh', dest='ssh', action='store_true',
        default=False, help='Use SSH when connecting to repos')
    parser.add_option('-q', '--quiet', dest='verbose', action='store_false',
        default=True, help='non-verbose output')
    parser.add_option('-d', '--debug', dest='debug', action='store_true',
        default=False, help='additional output for debugging')
    parser.add_option('-l', '--limit', dest='limit', metavar='PATH',
        help='only backup repos in the specified project/group (ex.: ' + \
        'MyProject) (or: MyProject/MyGroup)')
    parser.add_option('-u', '--update', dest='update', action='store_true',
        default=False, help='update working copy when cloning or pulling')

    (options, args) = parser.parse_args(args)

    # Get the destination directory, which should be the one and
    # only non-option argument.
    if len(args) == 0:
        parser.error('Must specify the destination directory for the backups.')
    if len(args) > 1:
        parser.error('Unknown arguments passed after destination directory')
    destination_dir = args[0]
    debug = options.debug

    # Now get any saved options from the configuration file and use
    # them to fill in any missing options.
    configfile_path = os.path.join(destination_dir, CONFIG_FILE)
    if os.path.exists(configfile_path):
        configfile = open(configfile_path, 'r')
        config_data = json.load(configfile)
        configfile.close()

        if not options.token and 'token' in config_data:
            options.token = config_data['token']

        if not options.server and 'server' in config_data:
            options.server = config_data['server']

    return (options, destination_dir)


def get_repos(server, token, ssh, verbose):
    """
    Query Kiln to get the list of available repositories. Return the
    list, sorted by the Kiln “full name” of each repository.
    """

    if verbose:
        print console_encode('Getting the list of repositories from %s' % server)
    
    url = '%s/Api/2.0/Project/?token=%s' % (server, token)
    if debug:
        print "get Projects: url:", url
    projects = json.load(urllib2.urlopen(url))
    if 'errors' in projects:
        sys.exit('Error from server: ' + projects['errors'][0]['sError'])

    ourRepos = []
    numHgRepos = 0
    numGitRepos = 0

    for project in projects:
        for repoGroup in project['repoGroups']:
            # watch for empty Groups
            if repoGroup['sSlug'] == '':
                repoGroup['sSlug'] = 'Group'
            if repoGroup['sName'] == '':
                repoGroup['sName'] = 'Group'
            for repo in repoGroup['repos']:
                # we'll get the appropriate full URL but also save the human-readable
                # names for sorting our list by VCS type, project, group, repo
                # Per https://developers.fogbugz.com/default.asp?W166:
                # VCS=1 is Mercurial (Hg), VCS=2 is Git

                localPath = project['sSlug'] + '/' + repoGroup['sSlug'] + '/' + repo['sSlug']

                if repo['vcs'] == 1:
                    numHgRepos += 1
                    if ssh:
                        repoPath = repo['sHgSshUrl']
                    else:
                        repoPath = repo['sHgUrl']
                elif repo['vcs'] == 2:
                    numGitRepos += 1
                    if ssh:
                        repoPath = repo['sGitSshUrl']
                    else:
                        repoPath = repo['sGitUrl']
                else:
                    sys.exit('Unknown VCS type for repo: ' + localPath) 
                
                if repo['sStatus'] != 'deleted':
                    ourRepos.append({'repoPath': repoPath, 'localPath': localPath, 'project': project['sName'], 
                        'repoGroup': repoGroup['sName'], 'repo': repo['sName'], 'vcs': repo['vcs']})

    if verbose:
        print console_encode('Found %d repositories (%d Hg, %d Git)' % (len(ourRepos), numHgRepos, numGitRepos))

    return sorted(ourRepos, key=itemgetter('vcs', 'project', 'repoGroup', 'repo'))


def backup_hg_repo(clone_url, target_dir, verbose, update):
    """
    Backup the specified Hg repository. Returns True if successful. If
    the backup fails, prints an error message and returns False.
    """
    backup_method = 'clone'

    # If the filesystem does not use Unicode (from Python’s
    # perspective), convert target_dir to plain ASCII.
    if not sys.getfilesystemencoding().upper().startswith('UTF'):
        target_dir = target_dir.encode('ascii', 'xmlcharrefreplace')

    # Does the target directory already exist?
    if os.path.isdir(target_dir):
        # Yes, it exists. Does it refer to the same repository?
        default = Popen(['hg', 'paths', '-R', target_dir, 'default'],
            stdout=PIPE, stderr=PIPE).communicate()[0].strip()
        default = urllib2.unquote(default)
        
        if default == clone_url:
            # It exists and is identical. We will pull.
            backup_method = 'pull'
        else:
            # It exists but refers to a different repo or is not a
            # repo. Move it to an archive directory.
            (parent_dir, repo_name) = os.path.split(target_dir)
            if verbose:
                print console_encode('exists but is not the same repo'),
                sys.stdout.flush()
            new_name = 'archive/%f-%s' % (time.time(), repo_name)
            new_dir = os.path.join(parent_dir, new_name)
            os.renames(target_dir, new_dir)
            if verbose:
                print console_encode('(archived); continuing backup...'),
                sys.stdout.flush()
    else:
        # Path doesn’t exist: create it
        os.makedirs(target_dir)

    # Back it up
    if backup_method == 'clone':
        if update:
            args = ['hg', 'clone', clone_url, target_dir]
        else:
            args = ['hg', 'clone', '--noupdate', clone_url, target_dir]
    else:
        if update:
            args = ['hg', 'pull', '-u', '-R', target_dir]
        else:
            args = ['hg', 'pull', '-R', target_dir]

    if debug:
        print "Backup Method:", backup_method
        print "args:", args

    # KilnAuth uses os.path.expanduser which should use
    # %USERPROFILE%. When run from Scheduled Tasks, it does not seem
    # to work unless you also set %HOMEDRIVE% and %HOMEPATH%.
    child_env = os.environ
    if os.name == 'nt':
        (drive, path) = os.path.splitdrive(os.environ['USERPROFILE'])
        child_env['HOMEDRIVE'] = drive
        child_env['HOMEPATH'] = path

    proc = Popen(args, stdout=PIPE, stderr=PIPE, env=child_env)
    (_, stderrdata) = proc.communicate()
    if proc.returncode:
        print console_encode('**** FAILED ****')
        print console_encode('*' * 60)
        print console_encode(u'Error backing up Hg repository %s\nError was: %s' %
            (clone_url, stderrdata))
        print console_encode('*' * 60)
        return False

    if verbose:
        print console_encode('backed up using Hg method %s' %
            backup_method.upper())

    return True


def backup_git_repo(clone_url, target_dir, verbose, update):
    """
    Backup the specified Git repository. Returns True if successful. If
    the backup fails, prints an error message and returns False.
    """
    backup_method = 'clone'

    # If the filesystem does not use Unicode (from Python’s
    # perspective), convert target_dir to plain ASCII.
    if not sys.getfilesystemencoding().upper().startswith('UTF'):
        target_dir = target_dir.encode('ascii', 'xmlcharrefreplace')

    # Does the target directory already exist?
    if os.path.isdir(target_dir):
        # Yes, it exists. Does it refer to the same repository?
        default = Popen(['git', '-C', target_dir, 'config', '--get', 'remote.origin.url'],
            stdout=PIPE, stderr=PIPE).communicate()[0].strip()
        default = urllib2.unquote(default)
        
        if default == clone_url:
            # It exists and is identical. We will pull.
            backup_method = 'pull'
        else:
            # It exists but refers to a different repo or is not a
            # repo. Move it to an archive directory.
            (parent_dir, repo_name) = os.path.split(target_dir)
            if verbose:
                print console_encode('exists but is not the same repo'),
                sys.stdout.flush()
            new_name = 'archive/%f-%s' % (time.time(), repo_name)
            new_dir = os.path.join(parent_dir, new_name)
            os.renames(target_dir, new_dir)
            if verbose:
                print console_encode('(archived); continuing backup...'),
                sys.stdout.flush()
    else:
        # Path doesn’t exist: create it
        os.makedirs(target_dir)

    # Back it up
    if backup_method == 'clone':
        if update:
            args = ['git', 'clone', clone_url, target_dir]
        else:
            args = ['git', 'clone', '--no-checkout', clone_url, target_dir]
    else:
        if update:
            args = ['git', '-C', target_dir, 'pull', target_dir]
        else:
            args = ['git', '-C', target_dir, 'fetch', target_dir]

    if debug:
        print "Backup Method:", backup_method
        print "args:", args

    # KilnAuth uses os.path.expanduser which should use
    # %USERPROFILE%. When run from Scheduled Tasks, it does not seem
    # to work unless you also set %HOMEDRIVE% and %HOMEPATH%.
    child_env = os.environ
    if os.name == 'nt':
        (drive, path) = os.path.splitdrive(os.environ['USERPROFILE'])
        child_env['HOMEDRIVE'] = drive
        child_env['HOMEPATH'] = path

    proc = Popen(args, stdout=PIPE, stderr=PIPE, env=child_env)
    (_, stderrdata) = proc.communicate()
    if proc.returncode:
        print console_encode('**** FAILED ****')
        print console_encode('*' * 60)
        print console_encode(u'Error backing up Git repository %s\nError was: %s' %
            (clone_url, stderrdata))
        print console_encode('*' * 60)
        return False

    if verbose:
        print console_encode('backed up using Git method %s' %
            backup_method.upper())

    return True


def console_encode(message):
    """
    Encodes the message as appropriate for output to sys.stdout.
    This is needed especially for Windows, where stdout is often a
    non-Unicode encoding.
    """
    if sys.stdout.encoding == None:
        # Encoding not available. Force ASCII.
        return unicode(message).encode('ASCII', 'xmlcharrefreplace')
    if sys.stdout.encoding.upper().startswith('UTF'):
        # Unicode console. No need to convert.
        return message
    else:
        return unicode(message).encode(sys.stdout.encoding,
            'xmlcharrefreplace')


def encode_url(url):
    """
    URLs returned by Kiln may have unencoded Unicode characters in
    them. Encode them.
    """
    url_parts = list(urlparse.urlsplit(url.encode('utf-8')))
    url_parts[2] = urllib.quote(url_parts[2])
    return urlparse.urlunsplit(url_parts)


def main():
    """
    Main entry point for Kiln backup utility.
    """

    # Parse the command line
    (options, destination_dir) = parse_command_line(sys.argv[1:])

    # If token or server were not specified, prompt the user.
    if not options.token:
        options.token = raw_input('Your FogBugz API token: ')
        if not options.token:
            sys.exit('FogBugz API token is required.')

    if not options.server:
        options.server = \
            raw_input('Kiln server name (e.g. company.kilnhg.com): ')
        if not options.server:
            sys.exit('Kiln server name is required.')

    # If the destination directory doesn’t exist, try to create it.
    if not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)
    if not os.path.isdir(destination_dir):
        sys.exit('Destination directory "' + destination_dir + '"' 
            + " doesn't exist and couldn't be created.")

    # Save configuration
    configfile = open(os.path.join(destination_dir, CONFIG_FILE), 'w+')
    config = {'server': options.server, 'token': options.token}
    json.dump(config, configfile, indent=4)
    configfile.write('\n')
    configfile.close()

    # Keep track of state for printing status messages
    if options.verbose:
        count = 0
        last_subdirectory = ''
        current_group = None

    # Keep track of overall success status. We continue backing up
    # even if there’s an error.
    overall_success = True
        
    # Back up the repositories
    repos = get_repos(options.server, options.token, options.ssh, options.verbose)

    if debug:
        print "Server:", options.server
        print "token:", options.token

    # If using --limit, filter repos we don’t want to backup.
    if options.limit:
        # Normalize the limit. Convert backslashes. Remove any
        # leading or trailing slash.
        limit = '%s' % options.limit.replace('\\', '/').strip('/')

        # Replace spaces with dashes, as Kiln does (in case the
        # user typed a human-readable repo name that has spaces)
        limit = limit.replace(' ', '-')

        # Filter. Case-insensitive. (Kiln won’t let you create two
        # groups or projects with the same name but different case.)
        repos = [_ for _ in repos if
            _['localPath'].lower().startswith(limit.lower())]

        if options.verbose:
            if len(repos) == 0:
                message = 'No repositories match the specified limit. '
                message += 'Nothing to back up!'
            else:
                if len(repos) == 1:
                    message = '1 repository matches '
                else:
                    message = '%d repositories match ' % len(repos)
                message += 'the specified limit and will be backed up'
            print console_encode(message)

    # Return an error code if there are no repos to back up. This
    # probably indicates a typo or similar mistake.
    if len(repos) == 0:
        overall_success = False

    for repo in repos:
        # Convert the repo path for subdirectory use if necessary
        # on Windows (/ to \).
        if os.sep == '/':
            subdirectory = repo['localPath']
        else:
            subdirectory = os.path.normpath(repo['localPath'])

        if options.verbose:
            # For the progress message, show the project and group
            # name as a header, and under that, list the repo names
            # being backed up. Also show a counter.

            group = os.path.commonprefix([last_subdirectory,
                os.path.dirname(subdirectory)])

            if group != current_group:
                current_group = os.path.dirname(subdirectory)
                print console_encode('\n%s' % current_group)

            last_subdirectory = subdirectory
            count += 1
            print console_encode('    >> [%d/%d] %s ' % (count, len(repos),
                os.path.basename(subdirectory))),

            # The following line fixes Issue 1. Python conveniently
            # inserts a space when a print statement ends with a
            # comma. Unfortunately if Mercurial is going to prompt
            # for a password, it does not know about the space that
            # is “supposed” to be there and the result looks like:
            # "reponamepassword:". The following line prevents
            # Python from inserting the space. Instead we manually
            # forced a space to the end of the print statement above.
            sys.stdout.write('')

            sys.stdout.flush()

        clone_url = encode_url(repo['repoPath'])
        target_dir = unicode(os.path.join(destination_dir, subdirectory))

        if debug:
            print
            print "repo['repoPath']:", repo['repoPath']
            print "clone_url:", clone_url
            print "target_dir:", target_dir
            print "options.verbose:", options.verbose
            print "options.update:", options.update

        if repo['vcs'] == 1:
            success = backup_hg_repo(clone_url, target_dir, options.verbose, options.update)
        elif repo['vcs'] == 2:
            success = backup_git_repo(clone_url, target_dir, options.verbose, options.update)
        overall_success = overall_success and success

    if overall_success:
        print 'All repositories backed up successfully.'
        return 0
    else:
        print 'Completed with errors.'
        return 1


if __name__ == '__main__':
    sys.exit(main())
