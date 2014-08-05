#
# keystone.py - Keystone Charm instructions
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

from cloudinstall.charms import CharmBase

log = logging.getLogger('cloudinstall.charms.keystone')


class CharmKeystone(CharmBase):
    """ Openstack Keystone directives """

    charm_name = 'keystone'
    display_name = 'Keystone'
    related = ['mysql']

    # must be > mysql or keystone will never deploy
    deploy_priority = 200

    def setup(self):
        mysql = self.wait_for_agent('mysql')
        if not mysql:
            log.debug("mysql not yet available, deferring keystone deploy")
            return True
        log.debug("mysql is available, deploying keystone")
        return super().setup()

__charm_class__ = CharmKeystone
