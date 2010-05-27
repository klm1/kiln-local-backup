#!/usr/bin/env python
# encoding: utf-8
"""
kiln_local_backup.py

Copyright (c) 2010 Nate Silva

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

import sys
import os
import urllib2
import time
import urllib
import urlparse
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


def parse_command_line(args):
    """
    Parse the command line arguments.

    Returns a tuple of (options, destination_dir).

    Calls sys.exit() if the command line could not be parsed.
    """

    usage = 'usage: %prog [options] DESTINATION-DIR'
    description = 'Backs up all available Mercurial repositories on Kiln ' + \
        'by cloning them (if they have not been backed up before), or by ' + \
        'pulling changes. In order to run this without user interaction, ' + \
        'you must install the FogBugz "KilnAuth" Mercurial extension and ' + \
        'clone at least one repository so that your credentials are saved.'

    parser = OptionParser(usage=usage, description=description)
    parser.formatter = IndentedHelpFormatter(max_help_position=30)

    parser.add_option('-t', '--token', dest='token', help='FogBugz API token')
    parser.add_option('-s', '--server', dest='server', help='Kiln server name')
    parser.add_option('--save', dest='save', action='store_true',
        default=False, help='save settings to the configuration file')
    parser.add_option('-q', '--quiet', dest='verbose', action='store_false',
        default=True, help='non-verbose output')

    (options, args) = parser.parse_args(args)

    # Get the destination directory, which should be the one and
    # only non-option argument.
    if len(args) == 0:
        parser.error('Must specify the destination directory for the backups.')
    if len(args) > 1:
        parser.error('Unknown arguments passed after destination directory')
    destination_dir = args[0]

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


def get_repos(server, token, verbose):
    """
    Query Kiln to get the list of available repositories. Return the
    list, sorted by the Kiln “full name” of each repository.
    """

    if verbose:
        print console_encode('Getting the list of repositories from %s' %
            server)

    url = 'https://%s/kiln/Api/Repos/?token=%s' % (server, token)
    data = json.load(urllib2.urlopen(url))
    if 'error' in data:
        sys.exit(data['error'])

    if verbose:
        print console_encode('Found %d repositories' % len(data))

    return sorted(data, lambda x, y: cmp(x['fullName'], y['fullName']))


def backup_repo(clone_url, target_dir, verbose):
    """
    Backup the specified repository. Returns True if successful. If
    the backup fails, prints an error message and returns False.
    """
    backup_method = 'clone'

    # If the filesystem does not use Unicode (from Python’s
    # perspective), convert target_dir to plain ASCII. Mainly
    # affects Windows. While we should be able to convert to
    # the filesystem encoding, I’ve found it doesn’t always work.
    if not sys.getfilesystemencoding().upper().startswith('UTF'):
        target_dir = target_dir.encode('ascii', 'xmlcharrefreplace')

    # Does the target directory already exist?
    if os.path.isdir(target_dir):
        # Yes, it exists. Does it refer to the same repository?
        default = Popen(['hg', 'paths', '-R', target_dir, 'default'],
            stdout=PIPE, stderr=PIPE).communicate()[0].strip()

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
        args = ['hg', 'clone', '--noupdate', clone_url, target_dir]
    else:
        args = ['hg', 'pull', '-R', target_dir]

    # KilnAuth uses the os.path.expanduser function. While that is
    # documented to use %USERPROFILE%, which is set, it does not
    # actually seem to do so when this script is run from Scheduled
    # Tasks. Instead it only appears to work if %HOMEDRIVE% and
    # %HOMEPATH% are set.
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
        print console_encode(u'Error backing up repository %s\nError was: %s' %
            (clone_url, stderrdata))
        print console_encode('*' * 60)
        return False

    if verbose:
        print console_encode('backed up using method %s' %
            backup_method.upper())

    return True


def console_encode(message):
    """
    Encodes the message as appropriate for output to sys.stdout.
    This is needed especially for Windows, where stdout is often a
    non-Unicode encoding.
    """
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
        sys.exit('destination directory', destination_dir, "doesn't exist",
            "and couldn't be created")

    # Save configuration if requested
    if options.save:
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
    # even if there’s an error. Windows Scheduled Tasks does not
    # notify the administrator if an error occurred, so if you had
    # a faulty repo, and we did not continue, you could miss a large
    # part of your backup and not know it. On Linux/Mac, cron will
    # e-mail the user when a script fails.
    overall_success = True

    # Back up the repositories
    repos = get_repos(options.server, options.token, options.verbose)
    for repo in repos:
        # The "full name" from Kiln is the project name, plus any
        # group name and the repo name. Components are separated by
        # a right angle quote (»). Turn this into a path by
        # converting angle quotes into path separators.
        parts = repo['fullName'].split(u'»')
        parts = [_.strip() for _ in parts]      # trim whitespace
        subdirectory = os.path.join(*parts)

        if options.verbose:
            # For the progress message, show the project and group
            # name as a header, and under that, list the repo names
            # being backed up. Also show a counter.

            # Get the current project/group value.
            group = os.path.commonprefix([last_subdirectory,
                os.path.dirname(subdirectory)])

            # Print the project/group header
            if group != current_group:
                current_group = os.path.dirname(subdirectory)
                print console_encode('\n%s' % current_group)

            # Print the repo name currently being backed up
            last_subdirectory = subdirectory
            count += 1
            print console_encode('    >> [%d/%d] %s' % (count, len(repos),
                os.path.basename(subdirectory))),
            sys.stdout.flush()

        clone_url = encode_url(repo['cloneUrl'].strip('"'))
        target_dir = unicode(os.path.join(destination_dir, subdirectory))

        success = backup_repo(clone_url, target_dir, options.verbose)
        overall_success = overall_success and success

    if overall_success:
        print 'All repositories backed up successfully.'
        return 0
    else:
        print 'Completed with errors.'
        return 1


if __name__ == '__main__':
    sys.exit(main())