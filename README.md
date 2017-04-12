# OVERVIEW

This is a backup script to maintain a copy of all of your Kiln Mercurial and Git 
repositories on your local system.

## GETTING STARTED

For Windows you need:

* A Kiln account
* A FogBugz API token
* Python 2.4 or higher (note that this script is not compatible with Python 3.x)
* simplejson (not needed with Python 2.6 or higher)
* The Kiln Tools installed

On other platforms, you’ll also need to install:

* Mecurial 1.3 or higher (if you have Hg repos in Kiln)
* Git 2.9 or higher (if you have Git repos in Kiln)
* The KilnAuth Mercurial extension

If you are missing any of these, see **REFERENCES** at the end of this document.

## BACKING UP

All repositories are backed up to the directory you specify. For example, if
you want to back up to C:\KilnBackups, run:

`python kiln-local-backup.py C:\KilnBackups`

You will be prompted for your API token and Kiln server name. If your 
credentials aren’t stored in KilnAuth, you’ll also be prompted for your 
password.

Your API token and server name are saved to a file in the backup directory,
so you won’t have to enter them next time.

Once KilnAuth has your credentials, you will not be prompted for your
password again.

To see the full syntax, including options for passing your API token and
server name on the command line, type:

`python kiln-local-backup.py --help`

## FAQS

Q: My backup is empty!

A: No, it’s not. With Mercurial and Git, the entire repository is stored 
in the .hg and .git subirectories respectively, which may be hidden.
You can clone the repository to see that the files are really there, 
if it makes you feel better.

Alternately, you can specify the `--update` command-line option. Then you’ll
see your working files. The working files that you see are just the latest
version of your project, but rest assured the script is backing up your
whole repository, including all history. That’s located in the .hg
subdirectory for Mercurial, and the .git directory for Git.


Q: Scheduled backups don’t work.

A: Make sure you are running the backup under **the same user account** you were
using when you entered your password for KilnAuth.


Q: What platforms does this run on?

A: I have tested it on Mac OS X 10.1, Windows Server 2008 R2, and Windows 10.


Q: Can I back up repositories with non-ASCII names?

A: It should work. On Windows, non-ASCII characters in the repository name
will be replaced with equivalent XML character references. On Unix/Mac,
non-ASCII names will be used as-is.

## REFERENCES

To get a FogBugz API token, see: http://goo.gl/buFUPB

To get the KilnAuth Mercurial extension, install the Kiln Client, or see your
Kiln Tools page. The URL is: http://[your kiln site]/Tools.

simplejson is only needed for Python versions earlier than 2.6. It can be
found at http://goo.gl/nv3tu.