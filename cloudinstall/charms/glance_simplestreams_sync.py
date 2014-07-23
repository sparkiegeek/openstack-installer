#
#
# Copyright 2014 Canonical, Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import logging
import os
import platform
import requests
import shutil
import subprocess

from cloudinstall.charms import (CharmBase, DisplayPriorities,
                                 CHARM_CONFIG,
                                 CHARM_CONFIG_FILENAME)

CHARM_STABLE_URL = ("https://github.com/Ubuntu-Solutions-Engineering/"
                    "glance-simplestreams-sync-charm/archive/stable.zip")

# Not necessarily required to match because we're local, but easy enough to get
CURRENT_DISTRO = platform.linux_distribution()[-1]
CHARMS_DIR = os.path.expanduser("~/.cloud-install/local-charms")

log = logging.getLogger(__name__)


class CharmGlanceSimplestreamsSync(CharmBase):
    """ Charm directives for glance-simplestreams-sync  """

    charm_name = 'glance-simplestreams-sync'
    display_name = 'Glance - Simplestreams Image Sync'
    display_priority = DisplayPriorities.Other
    related = ['keystone']

    def download_stable(self):
        if not os.path.exists(CHARMS_DIR):
            os.makedirs(CHARMS_DIR)

        r = requests.get(CHARM_STABLE_URL, verify=True)
        zf_name = os.path.join(CHARMS_DIR, 'stable.zip')
        with open(zf_name, mode='wb') as zf:
            zf.write(r.content)

        try:
            subprocess.check_output(['unzip', '-d', CHARMS_DIR,
                                     zf_name], stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            log.warning("error unzipping: rc={} out={}".format(e.returncode,
                                                               e.output))
            raise e

        src = os.path.join(CHARMS_DIR,
                           'glance-simplestreams-sync-charm-stable')
        dest = os.path.join(CHARMS_DIR, CURRENT_DISTRO,
                            'glance-simplestreams-sync')
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.renames(src, dest)

    def setup(self):
        """Temporary override to get local copy of charm."""

        log.debug("downloading stable branch from github")
        try:
            self.download_stable()
            log.debug("done: downloaded to " + CHARMS_DIR)

            log.debug("adding rabbitmq-server to relations list")
        except:
            log.exception("problem downloading stable branch."
                          " Falling back to charm store version.")
            super(CharmGlanceSimplestreamsSync, self).setup()
            return

        kwds = dict(machine_id=self.machine_id,
                    repodir=CHARMS_DIR,
                    distro=CURRENT_DISTRO)

        cmd = ('juju deploy --repository={repodir}'
               ' local:{distro}/glance-simplestreams-sync'
               ' --to {machine_id}').format(**kwds)

        if self.charm_name in CHARM_CONFIG:
            cmd += ' --config ' + CHARM_CONFIG_FILENAME

        try:
            log.debug("Deploying {} from local: {}".format(self.charm_name,
                                                           cmd))
            cmd_output = subprocess.check_output(cmd, stderr=subprocess.STDOUT,
                                                 shell=True)

            log.debug("Deploy output: " + cmd_output.decode('utf-8'))

        except subprocess.CalledProcessError as e:
            log.warning("Deploy error. rc={} out={}".format(e.returncode,
                                                            e.output))

    def set_relations(self):
        if os.path.exists(os.path.join(CHARMS_DIR, CURRENT_DISTRO,
                                       'glance-simplestreams-sync')):
            self.related.append('rabbitmq-server')
            log.debug("Added rabbitmq to relation list")

        return super(CharmGlanceSimplestreamsSync, self).set_relations()


__charm_class__ = CharmGlanceSimplestreamsSync
