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

from collections import defaultdict
import logging

from urwid import (AttrMap, Button, Columns, Divider, Filler,
                   GridFlow, LineBox, Overlay, Padding, Pile,
                   SelectableIcon, Text, WidgetWrap)

from cloudinstall.config import Config
from cloudinstall.machine import satisfies
from cloudinstall.ui import InfoDialog
from cloudinstall.utils import load_charms, format_constraint

log = logging.getLogger('cloudinstall.placement')


BUTTON_SIZE = 20


class PlaceholderMachine:
    """A dummy machine that doesn't map to an existing maas machine"""

    is_placeholder = True

    def __init__(self, instance_id, name):
        self.instance_id = instance_id
        self.system_id = instance_id
        self.display_name = name
        self.constraints = defaultdict(lambda: '*')

    @property
    def machine(self):
        return self.constraints

    @property
    def arch(self):
        return self.constraints['arch']

    @property
    def cpu_cores(self):
        return self.constraints['cpu_cores']

    @property
    def mem(self):
        return self.constraints['mem']

    @property
    def storage(self):
        return self.constraints['storage']

    @property
    def hostname(self):
        return self.display_name

    def __repr__(self):
        return "<Placeholder Machine: {}>".format(self.display_name)


class PlacementController:
    """Keeps state of current machines and their assigned services.
    """

    def __init__(self, maas_state, opts):
        self.maas_state = maas_state
        self.assignments = defaultdict(list)  # instance_id -> [charm class]
        self.opts = opts
        self.unplaced_services = set()

    def machines(self):
        return self.maas_state.machines()

    def charm_classes(self):
        cl = [m.__charm_class__ for m in load_charms()
              if not m.__charm_class__.optional and
              not m.__charm_class__.disabled]

        if self.opts.enable_swift:
            for m in load_charms():
                n = m.__charm_class__.name()
                if n == "swift-storage" or n == "swift-proxy":
                    cl.append(m.__charm_class__)
        return cl

    def are_assignments_equivalent(self, other):
        for mid, cl in self.assignments.items():
            if mid not in other:
                return False
            if set(cl) != set(other[mid]):
                return False
        return True

    def assign(self, machine, charm_class):
        if not charm_class.allow_multi_units:
            for m, l in self.assignments.items():
                if charm_class in l:
                    l.remove(charm_class)
        self.assignments[machine.instance_id].append(charm_class)
        self.reset_unplaced()

    def machines_for_charm(self, charm_class):
        all_machines = self.machines()
        machines = []
        for m_id, assignment_list in self.assignments.items():
            if charm_class in assignment_list:
                m = next((m for m in all_machines
                          if m.instance_id == m_id), None)
                machines.append(m)
        return machines

    def remove_assignment(self, m, cc):
        assignments = self.assignments[m.instance_id]
        assignments.remove(cc)
        self.reset_unplaced()

    def clear_all_assignments(self):
        self.assignments = defaultdict(list)
        self.reset_unplaced()

    def clear_assignments(self, m):
        del self.assignments[m.instance_id]
        self.reset_unplaced()

    def assignments_for_machine(self, m):
        return self.assignments[m.instance_id]

    def set_all_assignments(self, assignments):
        self.assignments = assignments
        self.reset_unplaced()

    def reset_unplaced(self):
        self.unplaced_services = set()
        for cc in self.charm_classes():
            ms = self.machines_for_charm(cc)
            if len(ms) == 0:
                self.unplaced_services.add(cc)

    def service_is_core(self, cc):
        uncore_services = ['swift-storage',
                           'swift-proxy',
                           'nova-compute']
        return cc.name() not in uncore_services

    def can_deploy(self):
        unplaced_cores = [cc for cc in self.unplaced_services
                          if self.service_is_core(cc)]

        return len(unplaced_cores) == 0

    def autoplace_unplaced_services(self):
        """Attempt to find machines for all unplaced services using only empty
        machines.

        Returns a pair (success, message) where success is True if all
        services are placed. message is an info message for the user.
        """

        empty_machines = [m for m in self.machines()
                          if len(self.assignments[m.instance_id]) == 0]

        unplaced_defaults = self.gen_defaults(list(self.unplaced_services),
                                              empty_machines)

        for mid, charm_classes in unplaced_defaults.items():
            self.assignments[mid] = charm_classes

        self.reset_unplaced()

        if len(self.unplaced_services) > 0:
            msg = ("Not enough empty machines could be found for the required"
                   " services. Please add machines or finish placement "
                   "manually.")
            return (False, msg)
        return (True, "")

    def gen_defaults(self, charm_classes=None, maas_machines=None):
        """Generates an assignments dictionary for the given charm classes and
        machines, based on constraints.

        Does not alter controller state.

        Use set_all_assignments(gen_defaults()) to clear and reset the
        controller's state to these defaults.

        """
        if charm_classes is None:
            charm_classes = self.charm_classes()

        assignments = defaultdict(list)

        if maas_machines is None:
            maas_machines = self.maas_state.machines()

        def satisfying_machine(constraints):
            for machine in maas_machines:
                if satisfies(machine, constraints)[0]:
                    maas_machines.remove(machine)
                    return machine

            return None

        isolated_charms, controller_charms = [], []

        for charm_class in charm_classes:
            if charm_class.isolate:
                isolated_charms.append(charm_class)
            else:
                controller_charms.append(charm_class)

        for charm_class in isolated_charms:
            m = satisfying_machine(charm_class.constraints)
            if m:
                assignments[m.instance_id].append(charm_class)

        controller_machine = satisfying_machine({})
        if controller_machine:
            for charm_class in controller_charms:
                assignments[controller_machine.instance_id].append(charm_class)

        return assignments


class MachineWidget(WidgetWrap):
    """A widget displaying a service and associated actions.

    machine - the machine to display

    controller - a PlacementController instance

    actions - a list of ('label', function) pairs that wil be used to
    create buttons for each machine.  The machine will be passed to
    the function as userdata.

    optionally, actions can be a 3-tuple (pred, 'label', function),
    where pred determines whether to add the button. Pred will be
    passed the charm class.

    show_hardware - display hardware details about this machine
    """

    def __init__(self, machine, controller, actions=None,
                 show_hardware=False):
        self.machine = machine
        self.controller = controller
        if actions is None:
            self.actions = []
        else:
            self.actions = actions
        self.show_hardware = show_hardware
        w = self.build_widgets()
        self.update()
        super().__init__(w)

    def selectable(self):
        return True

    def hardware_info_markup(self):
        m = self.machine
        return [('label', 'arch'), ' {}  '.format(m.arch),
                ('label', 'cores'), ' {}  '.format(m.cpu_cores),
                ('label', 'mem'), ' {}  '.format(m.mem),
                ('label', 'storage'), ' {}'.format(m.storage)]

    def build_widgets(self):
        if self.machine.instance_id == 'unplaced':
            self.machine_info_widget = Text(('info',
                                             "\N{DOTTED CIRCLE} Unplaced"))
        else:
            self.machine_info_widget = Text("\N{TAPE DRIVE} {}".format(
                self.machine.hostname))
        self.assignments_widget = Text("")

        self.hardware_widget = Text(["  "] + self.hardware_info_markup())

        self.buttons = []
        self.button_grid = GridFlow(self.buttons, 22, 2, 2, 'right')

        pl = [Divider(' '), self.machine_info_widget, self.assignments_widget]
        if self.show_hardware:
            pl.append(self.hardware_widget)
        pl.append(self.button_grid)

        p = Pile(pl)

        return Padding(p, left=2, right=2)

    def update(self):
        al = self.controller.assignments_for_machine(self.machine)
        astr = "  "
        if len(al) == 0:
            astr += "\N{EMPTY SET}"
        else:
            astr += ", ".join(["\N{GEAR} {}".format(c.display_name)
                               for c in al])

        self.assignments_widget.set_text(astr)
        self.update_buttons()

    def update_buttons(self):
        buttons = []
        for at in self.actions:
            if len(at) == 2:
                predicate = lambda x: True
                label, func = at
            else:
                predicate, label, func = at

            if not predicate(self.machine):
                b = AttrMap(SelectableIcon(" (" + label + ")"),
                            'disabled_button', 'disabled_button_focus')
            else:
                b = AttrMap(Button(label, on_press=func,
                                   user_data=self.machine),
                            'button', 'button_focus')
            buttons.append((b, self.button_grid.options()))

        self.button_grid.contents = buttons


class ServiceWidget(WidgetWrap):
    """A widget displaying a service and associated actions.

    charm_class - the class describing the service to display

    controller - a PlacementController instance

    actions - a list of ('label', function) pairs that wil be used to
    create buttons for each machine.  The machine will be passed to
    the function as userdata.

    optionally, actions can be a 3-tuple (pred, 'label', function),
    where pred determines whether to add the button. Pred will be
    passed the charm class.

    show_constraints - display the charm's constraints

    show_assignments - display the machine(s) currently assigned to
    host this service

    """

    def __init__(self, charm_class, controller, actions=None,
                 show_constraints=False, show_assignments=False,
                 extra_markup=None):
        self.charm_class = charm_class
        self.controller = controller
        if actions is None:
            self.actions = []
        else:
            self.actions = actions
        self.show_constraints = show_constraints
        self.show_assignments = show_assignments
        self.extra_markup = extra_markup
        w = self.build_widgets()
        self.update()
        super().__init__(w)

    def selectable(self):
        return True

    def build_widgets(self):
        title_markup = ["\N{GEAR} {}".format(self.charm_class.display_name)]
        if self.extra_markup:
            title_markup.append(self.extra_markup)

        self.charm_info_widget = Text(title_markup)
        self.assignments_widget = Text("")

        if len(self.charm_class.constraints) == 0:
            c_str = [('label', "  no constraints set")]
        else:
            cpairs = [format_constraint(k, v) for k, v in
                      self.charm_class.constraints.items()]
            c_str = [('label', "  constraints: "), ', '.join(cpairs)]
        self.constraints_widget = Text(c_str)

        self.buttons = []

        self.button_grid = GridFlow(self.buttons, 22, 1, 0, 'right')

        pl = [self.charm_info_widget]
        if self.show_assignments:
            pl.append(self.assignments_widget)
        if self.show_constraints:
            pl.append(self.constraints_widget)
        pl.append(self.button_grid)

        p = Pile(pl)
        return Padding(p, left=2, right=2)

    def update(self):
        ml = self.controller.machines_for_charm(self.charm_class)

        t = "  "
        if len(ml) == 0:
            t += "\N{DOTTED CIRCLE}"
        else:
            t += ", ".join(["\N{TAPE DRIVE} {}".format(m.hostname)
                            for m in ml])
        self.assignments_widget.set_text(t)

        self.update_buttons()

    def update_buttons(self):
        buttons = []
        for at in self.actions:
            if len(at) == 2:
                predicate = lambda x: True
                label, func = at
            else:
                predicate, label, func = at

            if not predicate(self.charm_class):
                b = AttrMap(SelectableIcon(" (" + label + ")"),
                            'disabled_button', 'disabled_button_focus')
            else:
                b = AttrMap(Button(label, on_press=func,
                                   user_data=self.charm_class),
                            'button', 'button_focus')
            buttons.append((b, self.button_grid.options()))

        self.button_grid.contents = buttons


class MachinesList(WidgetWrap):
    """A list of machines with configurable action buttons for each
    machine.

    actions - a list of ('label', function) pairs that wil be used to
    create buttons for each machine.  The machine will be passed to
    the function as userdata.

    constraints - a dict of constraints to filter the machines list.
    only machines matching all the constraints will be shown.

    show_hardware - bool, whether or not to show the hardware details
    for each of the machines

    """

    def __init__(self, controller, actions, constraints=None,
                 show_hardware=False):
        self.controller = controller
        self.actions = actions
        self.machine_widgets = []
        if constraints is None:
            self.constraints = {}
        else:
            self.constraints = constraints
        self.show_hardware = show_hardware
        w = self.build_widgets()
        self.update()
        super().__init__(w)

    def selectable(self):
        # overridden to ensure that we can arrow through the buttons
        # shouldn't be necessary according to documented behavior of
        # Pile & Columns, but discovered via trial & error.
        return True

    def build_widgets(self):
        if len(self.constraints) > 0:
            cstr = " matching constraints"
        else:
            cstr = ""
        self.machine_pile = Pile([Text("Machines" + cstr)] +
                                 self.machine_widgets)
        return self.machine_pile

    def update(self):

        def find_widget(m):
            return next((mw for mw in self.machine_widgets if
                         mw.machine.instance_id == m.instance_id), None)

        for m in self.controller.machines():
            if not satisfies(m, self.constraints)[0]:
                continue
            mw = find_widget(m)
            if mw is None:
                mw = self.add_machine_widget(m)
            mw.update()

    def add_machine_widget(self, machine):
        mw = MachineWidget(machine, self.controller, self.actions,
                           self.show_hardware)
        self.machine_widgets.append(mw)
        options = self.machine_pile.options()
        self.machine_pile.contents.append((mw, options))

        self.machine_pile.contents.append((AttrMap(Padding(Divider('\u23bc'),
                                                           left=2, right=2),
                                                   'label'), options))
        return mw


class ServicesList(WidgetWrap):
    """A list of services (charm classes) with configurable action buttons
    for each machine.

    actions - a list of tuples describing buttons. Passed to
    ServiceWidget.

    machine - a machine instance to query for constraint checking

    show_constraints - bool, whether or not to show the constraints
    for the various services

    """

    def __init__(self, controller, actions, machine=None,
                 unplaced_only=False, show_constraints=False):
        self.controller = controller
        self.actions = actions
        self.service_widgets = []
        self.machine = machine
        self.unplaced_only = unplaced_only
        self.show_constraints = show_constraints
        w = self.build_widgets()
        self.update()
        super().__init__(w)

    def selectable(self):
        # overridden to ensure that we can arrow through the buttons
        # shouldn't be necessary according to documented behavior of
        # Pile & Columns, but discovered via trial & error.
        return True

    def build_widgets(self):
        self.service_pile = Pile([Text("Services")] +
                                 self.service_widgets)
        return self.service_pile

    def find_service_widget(self, cc):
        return next((sw for sw in self.service_widgets if
                     sw.charm_class.charm_name == cc.charm_name), None)

    def update(self):
        for cc in self.controller.charm_classes():
            if self.machine and not satisfies(self.machine,
                                              cc.constraints)[0]:
                self.remove_service_widget(cc)
                continue

            if self.unplaced_only and \
               cc not in self.controller.unplaced_services:
                self.remove_service_widget(cc)
                continue

            sw = self.find_service_widget(cc)
            if sw is None:
                sw = self.add_service_widget(cc)
            sw.update()

    def add_service_widget(self, charm_class):
        if self.unplaced_only and self.controller.service_is_core(charm_class):
            extra = ('info', " (REQUIRED)")
        else:
            extra = None
        sw = ServiceWidget(charm_class, self.controller, self.actions,
                           self.show_constraints,
                           extra_markup=extra)
        self.service_widgets.append(sw)
        options = self.service_pile.options()
        self.service_pile.contents.append((sw, options))
        self.service_pile.contents.append((AttrMap(Padding(Divider('\u23bc'),
                                                           left=2, right=2),
                                                   'label'), options))
        return sw

    def remove_service_widget(self, charm_class):
        sw = self.find_service_widget(charm_class)

        if sw is None:
            return
        self.service_widgets.remove(sw)
        sw_idx = 0
        for w, opts in self.service_pile.contents:
            if w == sw:
                break
            sw_idx += 1

        c = self.service_pile.contents[:sw_idx] + \
            self.service_pile.contents[sw_idx + 2:]
        self.service_pile.contents = c


class MachineChooser(WidgetWrap):
    """Presents list of machines to assign a service to.
    Supports multiple selection if the service does.
    """

    def __init__(self, controller, charm_class, parent_widget):
        self.controller = controller
        self.charm_class = charm_class
        self.parent_widget = parent_widget
        w = self.build_widgets()
        super().__init__(w)

    def build_widgets(self):

        if self.charm_class.allow_multi_units:
            machine_string = "machines"
            plural_string = "s"
        else:
            machine_string = "a machine"
            plural_string = ""
        instructions = Text("Select {} to host {}".format(
            machine_string, self.charm_class.display_name))

        self.service_widget = ServiceWidget(self.charm_class,
                                            self.controller,
                                            show_constraints=True,
                                            show_assignments=True)

        constraints = self.charm_class.constraints
        self.machines_list = MachinesList(self.controller,
                                          [('Select', self.do_select)],
                                          constraints=constraints,
                                          show_hardware=True)
        self.machines_list.update()
        close_button = AttrMap(Button('Close',
                                      on_press=self.close_pressed),
                               'button', 'button_focus')
        p = Pile([instructions, Divider(), self.service_widget,
                  Divider(), self.machines_list,
                  GridFlow([close_button],
                           BUTTON_SIZE, 1, 0, 'right')])

        return LineBox(p, title="Select Machine{}".format(plural_string))

    def do_select(self, sender, machine):
        self.controller.assign(machine, self.charm_class)
        self.machines_list.update()
        self.service_widget.update()

    def close_pressed(self, sender):
        self.parent_widget.remove_overlay(self)


class ServiceChooser(WidgetWrap):
    """Presents list of services to put on a machine.

    Supports multiple selection, implying separate containers using
    --to.

    """

    def __init__(self, controller, machine, parent_widget):
        self.controller = controller
        self.machine = machine
        self.parent_widget = parent_widget
        w = self.build_widgets()
        super().__init__(w)

    def build_widgets(self):

        instructions = Text("Select services to add to {}".format(
            self.machine.hostname))

        self.machine_widget = MachineWidget(self.machine,
                                            self.controller,
                                            show_hardware=True)

        def show_remove_p(cc):
            ms = self.controller.machines_for_charm(cc)
            hostnames = [m.hostname for m in ms]
            return self.machine.hostname in hostnames

        def show_add_p(cc):
            ms = self.controller.machines_for_charm(cc)
            hostnames = [m.hostname for m in ms]
            return (self.machine.hostname not in hostnames
                    or cc.allow_multi_units)

        add_label = "Add to {}".format(self.machine.hostname)

        self.services_list = ServicesList(self.controller,
                                          [(show_add_p, add_label,
                                            self.do_add),
                                           (show_remove_p, 'Remove',
                                            self.do_remove)],
                                          machine=self.machine,
                                          show_constraints=True)

        close_button = AttrMap(Button('Close',
                                      on_press=self.close_pressed),
                               'button', 'button_focus')
        p = Pile([instructions, Divider(), self.machine_widget,
                  Divider(), self.services_list,
                  GridFlow([close_button],
                           BUTTON_SIZE, 1, 0, 'right')])

        return LineBox(p, title="Select Services")

    def update(self):
        self.machine_widget.update()
        self.services_list.update()

    def do_add(self, sender, charm_class):
        self.controller.assign(self.machine, charm_class)
        self.update()

    def do_remove(self, sender, charm_class):
        self.controller.remove_assignment(self.machine,
                                          charm_class)
        self.update()

    def close_pressed(self, sender):
        self.parent_widget.remove_overlay(self)


class ServicesColumn(WidgetWrap):
    """Displays dynamic list of unplaced services and associated controls
    """
    def __init__(self, display_controller, placement_controller,
                 placement_view):
        self.display_controller = display_controller
        self.placement_controller = placement_controller
        self.placement_view = placement_view
        w = self.build_widgets()
        super().__init__(w)
        self.update()

    def selectable(self):
        return True

    def build_widgets(self):
        actions = [("Choose Machine",
                    self.placement_view.do_show_machine_chooser)]
        self.unplaced_services_list = ServicesList(self.placement_controller,
                                                   actions,
                                                   unplaced_only=True,
                                                   show_constraints=True)
        autoplace_func = self.placement_view.do_autoplace
        self.autoplace_button = AttrMap(Button("Auto-place remaining services",
                                               on_press=autoplace_func),
                                        'button', 'button_focus')
        self.reset_button = AttrMap(Button("Reset to default placement",
                                           on_press=self.do_reset_to_defaults),
                                    'button', 'button_focus')
        self.unplaced_services_pile = Pile([self.unplaced_services_list,
                                            Divider()])

        self.bottom_buttons = []
        self.bottom_button_grid = GridFlow(self.bottom_buttons,
                                           36, 1, 0, 'center')

        # placeholders replaced in update():
        pl = [Pile([]),         # unplaced services
              self.bottom_button_grid]

        self.main_pile = Pile(pl)

        return self.main_pile

    def update(self):
        self.unplaced_services_list.update()

        bottom_buttons = []

        if len(self.placement_controller.unplaced_services) == 0:
            self.main_pile.contents[0] = (Divider(),
                                          self.main_pile.options())
            icon = SelectableIcon(" (Auto-place remaining services) ")
            bottom_buttons.append((AttrMap(icon,
                                           'disabled_button',
                                           'disabled_button_focus'),
                                   self.bottom_button_grid.options()))

        else:
            self.main_pile.contents[0] = (self.unplaced_services_pile,
                                          self.main_pile.options())
            bottom_buttons.append((self.autoplace_button,
                                   self.bottom_button_grid.options()))

        defs = self.placement_controller.gen_defaults()

        if self.placement_controller.are_assignments_equivalent(defs):
            icon = SelectableIcon(" (Reset to default placement) ")
            bottom_buttons.append((AttrMap(icon,
                                           'disabled_button',
                                           'disabled_button_focus'),
                                   self.bottom_button_grid.options()))
        else:
            bottom_buttons.append((self.reset_button,
                                  self.bottom_button_grid.options()))

        self.bottom_button_grid.contents = bottom_buttons

    def do_reset_to_defaults(self, sender):
        self.placement_controller.set_all_assignments(
            self.placement_controller.gen_defaults())


class HeaderView(WidgetWrap):

    def __init__(self, display_controller, placement_controller,
                 placement_view):
        self.display_controller = display_controller
        self.placement_controller = placement_controller
        self.placement_view = placement_view
        w = self.build_widgets()
        super().__init__(w)
        self.update()

    def selectable(self):
        return True

    def build_widgets(self):
        deploy_ok_msg = Text([('success_icon', '\u2713'),
                              " All the core OpenStack services are placed"
                              " on a machine, and you can now deploy."])

        self.deploy_button = AttrMap(Button("Deploy",
                                            on_press=self.do_deploy),
                                     'deploy_button', 'deploy_button_focus')
        self.deploy_grid = GridFlow([self.deploy_button], 10, 1, 0, 'center')
        self.deploy_widgets = Pile([Padding(deploy_ok_msg,
                                            align='center',
                                            width='pack'),
                                    self.deploy_grid])

        unplaced_msg = "Some core services are still unplaced."
        self.unplaced_warning_widgets = Padding(Text([('error_icon',
                                                       "\N{WARNING SIGN} "),
                                                      unplaced_msg]),
                                                align='center',
                                                width='pack')

        self.main_pile = Pile([Divider(),
                               Padding(Text("Machine Placement"),
                                       align='center',
                                       width='pack'),
                               Pile([]),
                               Divider()])
        return self.main_pile

    def update(self):
        if self.placement_controller.can_deploy():
            self.main_pile.contents[2] = (self.deploy_widgets,
                                          self.main_pile.options())
        else:
            self.main_pile.contents[2] = (self.unplaced_warning_widgets,
                                          self.main_pile.options())

    def do_deploy(self, sender):
        self.display_controller.commit_placement()


class MachinesColumn(WidgetWrap):
    """Shows machines"""
    def __init__(self, display_controller, placement_controller,
                 placement_view):
        self.display_controller = display_controller
        self.placement_controller = placement_controller
        self.placement_view = placement_view
        self.config = Config()
        w = self.build_widgets()
        super().__init__(w)
        self.update()

    def selectable(self):
        return True

    def build_widgets(self):

        def show_clear_p(m):
            pc = self.placement_controller
            return len(pc.assignments_for_machine(m)) != 0

        clear_machine_func = self.placement_view.do_clear_machine
        show_chooser_func = self.placement_view.do_show_service_chooser
        self.machines_list = MachinesList(self.placement_controller,
                                          [(show_clear_p,
                                            'Clear', clear_machine_func),
                                           ('Edit Services',
                                            show_chooser_func)],
                                          show_hardware=True)
        self.machines_list.update()

        self.machines_list_pile = Pile([self.machines_list,
                                        Divider()])

        clear_all_func = self.placement_view.do_clear_all
        self.clear_all_button = AttrMap(Button("Clear all Machines",
                                               on_press=clear_all_func),
                                        'button', 'button_focus')

        bc = self.config.juju_env['bootstrap-config']
        maasname = "'{}' ({})".format(bc['name'], bc['maas-server'])

        openlabel = "Open {} in browser".format(bc['maas-server'])
        self.open_maas_button = AttrMap(Button(openlabel,
                                               on_press=self.browse_maas),
                                        'button', 'button_focus')

        self.bottom_buttons = []
        self.bottom_button_grid = GridFlow(self.bottom_buttons,
                                           36, 1, 0, 'center')

        header = Padding(Text("You are connected to MAAS {}".format(maasname)),
                         align='center',
                         width='pack')

        # placeholders replaced in update():
        pl = [header,
              Pile([]),         # machines_list
              Divider(),
              self.bottom_button_grid]

        self.main_pile = Pile(pl)

        return self.main_pile

    def update(self):
        self.machines_list.update()

        bottom_buttons = []

        empty_maas_msg = ("There are no available machines.")

        self.empty_maas_widgets = Padding(Text([('error_icon',
                                                 "\N{WARNING SIGN} "),
                                                empty_maas_msg]),
                                          align='center',
                                          width='pack')

        if len(self.placement_controller.machines()) == 0:
            self.main_pile.contents[1] = (self.empty_maas_widgets,
                                          self.main_pile.options())
            bottom_buttons.append((self.open_maas_button,
                                   self.bottom_button_grid.options()))

        else:
            self.main_pile.contents[1] = (self.machines_list_pile,
                                          self.main_pile.options())
            bottom_buttons.append((self.clear_all_button,
                                   self.bottom_button_grid.options()))

        self.bottom_button_grid.contents = bottom_buttons

    def browse_maas(self, sender):
        pass  # TODO


class PlacementView(WidgetWrap):
    """Handles display of machines and services.

    displays nothing if self.controller is not set.
    set it to a PlacementController.
    """

    def __init__(self, display_controller, placement_controller):
        self.display_controller = display_controller
        self.placement_controller = placement_controller
        w = self.build_widgets()
        super().__init__(w)
        self.update()

    def scroll_down(self):
        pass

    def scroll_up(self):
        pass

    def build_widgets(self):
        self.header_view = HeaderView(self.display_controller,
                                      self.placement_controller,
                                      self)

        self.services_column = ServicesColumn(self.display_controller,
                                              self.placement_controller,
                                              self)

        self.machines_column = MachinesColumn(self.display_controller,
                                              self.placement_controller,
                                              self)

        self.columns = Columns([self.services_column,
                                self.machines_column])
        self.main_pile = Pile([Padding(self.header_view,
                                       align='center',
                                       width=('relative', 50)),
                               Padding(self.columns,
                                       align='center',
                                       width=('relative', 95))])
        return Filler(self.main_pile, valign='top')

    def update(self):
        self.header_view.update()
        self.services_column.update()
        self.machines_column.update()

    def do_autoplace(self, sender):
        ok, msg = self.placement_controller.autoplace_unplaced_services()
        if not ok:
            self.show_overlay(Filler(InfoDialog(msg,
                                                self.remove_overlay)))

    def do_clear_all(self, sender):
        self.placement_controller.clear_all_assignments()

    def do_clear_machine(self, sender, machine):
        self.placement_controller.clear_assignments(machine)

    def do_clear_service(self, sender, charm_class):
        for m in self.placement_controller.machines_for_charm(charm_class):
            self.placement_controller.remove_assignment(m, charm_class)

    def do_show_service_chooser(self, sender, machine):
        self.show_overlay(Filler(ServiceChooser(self.placement_controller,
                                                machine,
                                                self)))

    def do_show_machine_chooser(self, sender, charm_class):
        self.show_overlay(Filler(MachineChooser(self.placement_controller,
                                                charm_class,
                                                self)))

    def show_overlay(self, overlay_widget):
        self.orig_w = self._w
        self._w = Overlay(top_w=overlay_widget,
                          bottom_w=self._w,
                          align='center',
                          width=('relative', 60),
                          min_width=80,
                          valign='middle',
                          height=('relative', 80))

    def remove_overlay(self, overlay_widget):
        # urwid note: we could also get orig_w as
        # self._w.contents[0][0], but this is clearer:
        self._w = self.orig_w