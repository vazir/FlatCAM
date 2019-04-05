from PyQt5 import QtGui, QtCore, QtWidgets
from PyQt5.QtCore import Qt, QSettings

from shapely.geometry import LineString, LinearRing, MultiLineString
from shapely.ops import cascaded_union
import shapely.affinity as affinity

from numpy import arctan2, Inf, array, sqrt, sign, dot
from rtree import index as rtindex
import threading, time
import copy

from camlib import *
from flatcamGUI.GUIElements import FCEntry, FCComboBox, FCTable, FCDoubleSpinner, LengthEntry, RadioSet, SpinBoxDelegate
from flatcamEditors.FlatCAMGeoEditor import FCShapeTool, DrawTool, DrawToolShape, DrawToolUtilityShape, FlatCAMGeoEditor

import gettext
import FlatCAMTranslation as fcTranslate

fcTranslate.apply_language('strings')
import builtins
if '_' not in builtins.__dict__:
    _ = gettext.gettext


class FCApertureResize(FCShapeTool):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'aperture_resize'

        self.draw_app.app.inform.emit(_("Click on the Apertures to resize ..."))
        self.resize_dia = None
        self.draw_app.resize_frame.show()
        self.points = None
        self.selected_dia_list = []
        self.current_storage = None
        self.geometry = []
        self.destination_storage = None

        self.draw_app.resize_btn.clicked.connect(self.make)

        # Switch notebook to Selected page
        self.draw_app.app.ui.notebook.setCurrentWidget(self.draw_app.app.ui.selected_tab)

    def make(self):
        self.draw_app.is_modified = True

        try:
            new_dia = self.draw_app.resdrill_entry.get_value()
        except:
            self.draw_app.app.inform.emit(_("[ERROR_NOTCL] Resize drill(s) failed. Please enter a diameter for resize."))
            return

        if new_dia not in self.draw_app.olddia_newdia:
            self.destination_storage = FlatCAMGeoEditor.make_storage()
            self.draw_app.storage_dict[new_dia] = self.destination_storage

            # self.olddia_newdia dict keeps the evidence on current tools diameters as keys and gets updated on values
            # each time a tool diameter is edited or added
            self.draw_app.olddia_newdia[new_dia] = new_dia
        else:
            self.destination_storage = self.draw_app.storage_dict[new_dia]

        for index in self.draw_app.apertures_table.selectedIndexes():
            row = index.row()
            # on column 1 in tool tables we hold the diameters, and we retrieve them as strings
            # therefore below we convert to float
            dia_on_row = self.draw_app.apertures_table.item(row, 1).text()
            self.selected_dia_list.append(float(dia_on_row))

        # since we add a new tool, we update also the intial state of the tool_table through it's dictionary
        # we add a new entry in the tool2tooldia dict
        self.draw_app.tool2tooldia[len(self.draw_app.olddia_newdia)] = new_dia

        sel_shapes_to_be_deleted = []

        for sel_dia in self.selected_dia_list:
            self.current_storage = self.draw_app.storage_dict[sel_dia]
            for select_shape in self.draw_app.get_selected():
                if select_shape in self.current_storage.get_objects():
                    factor = new_dia / sel_dia
                    self.geometry.append(
                        DrawToolShape(affinity.scale(select_shape.geo, xfact=factor, yfact=factor, origin='center'))
                    )
                    self.current_storage.remove(select_shape)
                    # a hack to make the tool_table display less drills per diameter when shape(drill) is deleted
                    # self.points_edit it's only useful first time when we load the data into the storage
                    # but is still used as reference when building tool_table in self.build_ui()
                    # the number of drills displayed in column 2 is just a len(self.points_edit) therefore
                    # deleting self.points_edit elements (doesn't matter who but just the number)
                    # solved the display issue.
                    del self.draw_app.points_edit[sel_dia][0]

                    sel_shapes_to_be_deleted.append(select_shape)

                    self.draw_app.on_exc_shape_complete(self.destination_storage)
                    # a hack to make the tool_table display more drills per diameter when shape(drill) is added
                    # self.points_edit it's only useful first time when we load the data into the storage
                    # but is still used as reference when building tool_table in self.build_ui()
                    # the number of drills displayed in column 2 is just a len(self.points_edit) therefore
                    # deleting self.points_edit elements (doesn't matter who but just the number)
                    # solved the display issue.
                    if new_dia not in self.draw_app.points_edit:
                        self.draw_app.points_edit[new_dia] = [(0, 0)]
                    else:
                        self.draw_app.points_edit[new_dia].append((0,0))
                    self.geometry = []

                    # if following the resize of the drills there will be no more drills for the selected tool then
                    # delete that tool
                    if not self.draw_app.points_edit[sel_dia]:
                        self.draw_app.on_tool_delete(sel_dia)

            for shp in sel_shapes_to_be_deleted:
                self.draw_app.selected.remove(shp)
            sel_shapes_to_be_deleted = []

        self.draw_app.build_ui()
        self.draw_app.replot()

        self.draw_app.resize_frame.hide()
        self.complete = True
        self.draw_app.app.inform.emit(_("[success] Done. Drill Resize completed."))

        # MS: always return to the Select Tool
        self.draw_app.select_tool("select")


class FCApertureMove(FCShapeTool):
    def __init__(self, draw_app):
        DrawTool.__init__(self, draw_app)
        self.name = 'aperture_move'

        # self.shape_buffer = self.draw_app.shape_buffer
        self.origin = None
        self.destination = None
        self.selected_dia_list = []

        if self.draw_app.launched_from_shortcuts is True:
            self.draw_app.launched_from_shortcuts = False
            self.draw_app.app.inform.emit(_("Click on target location ..."))
        else:
            self.draw_app.app.inform.emit(_("Click on reference location ..."))
        self.current_storage = None
        self.geometry = []

        for index in self.draw_app.apertures_table.selectedIndexes():
            row = index.row()
            # on column 1 in tool tables we hold the diameters, and we retrieve them as strings
            # therefore below we convert to float
            dia_on_row = self.draw_app.apertures_table.item(row, 1).text()
            self.selected_dia_list.append(float(dia_on_row))

        # Switch notebook to Selected page
        self.draw_app.app.ui.notebook.setCurrentWidget(self.draw_app.app.ui.selected_tab)

    def set_origin(self, origin):
        self.origin = origin

    def click(self, point):
        if len(self.draw_app.get_selected()) == 0:
            return "Nothing to move."

        if self.origin is None:
            self.set_origin(point)
            self.draw_app.app.inform.emit(_("Click on target location ..."))
            return
        else:
            self.destination = point
            self.make()

            # MS: always return to the Select Tool
            self.draw_app.select_tool("select")
            return

    def make(self):
        # Create new geometry
        dx = self.destination[0] - self.origin[0]
        dy = self.destination[1] - self.origin[1]
        sel_shapes_to_be_deleted = []

        for sel_dia in self.selected_dia_list:
            self.current_storage = self.draw_app.storage_dict[sel_dia]
            for select_shape in self.draw_app.get_selected():
                if select_shape in self.current_storage.get_objects():

                    self.geometry.append(DrawToolShape(affinity.translate(select_shape.geo, xoff=dx, yoff=dy)))
                    self.current_storage.remove(select_shape)
                    sel_shapes_to_be_deleted.append(select_shape)
                    self.draw_app.on_exc_shape_complete(self.current_storage)
                    self.geometry = []

            for shp in sel_shapes_to_be_deleted:
                self.draw_app.selected.remove(shp)
            sel_shapes_to_be_deleted = []

        self.draw_app.build_ui()
        self.draw_app.app.inform.emit(_("[success] Done. Drill(s) Move completed."))

    def utility_geometry(self, data=None):
        """
        Temporary geometry on screen while using this tool.

        :param data:
        :return:
        """
        geo_list = []

        if self.origin is None:
            return None

        if len(self.draw_app.get_selected()) == 0:
            return None

        dx = data[0] - self.origin[0]
        dy = data[1] - self.origin[1]
        for geom in self.draw_app.get_selected():
            geo_list.append(affinity.translate(geom.geo, xoff=dx, yoff=dy))
        return DrawToolUtilityShape(geo_list)


class FCApertureCopy(FCApertureMove):
    def __init__(self, draw_app):
        FCApertureMove.__init__(self, draw_app)
        self.name = 'aperture_copy'

    def make(self):
        # Create new geometry
        dx = self.destination[0] - self.origin[0]
        dy = self.destination[1] - self.origin[1]
        sel_shapes_to_be_deleted = []

        for sel_dia in self.selected_dia_list:
            self.current_storage = self.draw_app.storage_dict[sel_dia]
            for select_shape in self.draw_app.get_selected():
                if select_shape in self.current_storage.get_objects():
                    self.geometry.append(DrawToolShape(affinity.translate(select_shape.geo, xoff=dx, yoff=dy)))

                    # add some fake drills into the self.draw_app.points_edit to update the drill count in tool table
                    self.draw_app.points_edit[sel_dia].append((0, 0))

                    sel_shapes_to_be_deleted.append(select_shape)
                    self.draw_app.on_exc_shape_complete(self.current_storage)
                    self.geometry = []

            for shp in sel_shapes_to_be_deleted:
                self.draw_app.selected.remove(shp)
            sel_shapes_to_be_deleted = []

        self.draw_app.build_ui()
        self.draw_app.app.inform.emit(_("[success] Done. Drill(s) copied."))


class FCApertureSelect(DrawTool):
    def __init__(self, grb_editor_app):
        DrawTool.__init__(self, grb_editor_app)
        self.name = 'drill_select'

        self.grb_editor_app = grb_editor_app
        self.storage = self.grb_editor_app.storage_dict
        # self.selected = self.grb_editor_app.selected

        # here we store all shapes that were selected
        self.sel_storage = []

        self.grb_editor_app.resize_frame.hide()
        self.grb_editor_app.array_frame.hide()

    def click(self, point):
        key_modifier = QtWidgets.QApplication.keyboardModifiers()
        if self.grb_editor_app.app.defaults["global_mselect_key"] == 'Control':
            if key_modifier == Qt.ControlModifier:
                pass
            else:
                self.grb_editor_app.selected = []
        else:
            if key_modifier == Qt.ShiftModifier:
                pass
            else:
                self.grb_editor_app.selected = []

    def click_release(self, point):
        self.select_shapes(point)
        return ""

    def select_shapes(self, pos):
        self.grb_editor_app.apertures_table.clearSelection()

        for storage in self.grb_editor_app.storage_dict:
            for shape in self.grb_editor_app.storage_dict[storage]:
                if Point(pos).within(shape.geo):
                    self.sel_storage.append(DrawToolShape(shape.geo))

        if pos[0] < xmin or pos[0] > xmax or pos[1] < ymin or pos[1] > ymax:
            self.grb_editor_app.selected = []
        else:
            key_modifier = QtWidgets.QApplication.keyboardModifiers()
            if self.grb_editor_app.app.defaults["global_mselect_key"] == 'Control':
                # if CONTROL key is pressed then we add to the selected list the current shape but if it's already
                # in the selected list, we removed it. Therefore first click selects, second deselects.
                if key_modifier == Qt.ControlModifier:
                    if closest_shape in self.grb_editor_app.selected:
                        self.grb_editor_app.selected.remove(closest_shape)
                    else:
                        self.grb_editor_app.selected.append(closest_shape)
                else:
                    self.grb_editor_app.selected = []
                    self.grb_editor_app.selected.append(closest_shape)
            else:
                if key_modifier == Qt.ShiftModifier:
                    if closest_shape in self.grb_editor_app.selected:
                        self.grb_editor_app.selected.remove(closest_shape)
                    else:
                        self.grb_editor_app.selected.append(closest_shape)
                else:
                    self.grb_editor_app.selected = []
                    self.grb_editor_app.selected.append(closest_shape)

            # select the aperture of the selected shape in the tool table
            for storage in self.grb_editor_app.storage_dict:
                for shape_s in self.grb_editor_app.selected:
                    if shape_s in self.grb_editor_app.storage_dict[storage]:
                        for key in self.grb_editor_app.tool2tooldia:
                            if self.grb_editor_app.tool2tooldia[key] == storage:
                                item = self.grb_editor_app.apertures_table.item((key - 1), 1)
                                self.grb_editor_app.apertures_table.setCurrentItem(item)
                                # item.setSelected(True)
                                # self.grb_editor_app.apertures_table.selectItem(key - 1)
                                # midx = self.grb_editor_app.apertures_table.model().index((key - 1), 0)
                                # self.grb_editor_app.apertures_table.setCurrentIndex(midx)
                                self.draw_app.last_tool_selected = key
        # delete whatever is in selection storage, there is no longer need for those shapes
        self.sel_storage = []

        return ""


class FlatCAMGrbEditor(QtCore.QObject):

    draw_shape_idx = -1

    def __init__(self, app):
        assert isinstance(app, FlatCAMApp.App), \
            "Expected the app to be a FlatCAMApp.App, got %s" % type(app)

        super(FlatCAMGrbEditor, self).__init__()

        self.app = app
        self.canvas = self.app.plotcanvas

        ## Current application units in Upper Case
        self.units = self.app.ui.general_defaults_form.general_app_group.units_radio.get_value().upper()

        self.grb_edit_widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        self.grb_edit_widget.setLayout(layout)

        ## Page Title box (spacing between children)
        self.title_box = QtWidgets.QHBoxLayout()
        layout.addLayout(self.title_box)

        ## Page Title icon
        pixmap = QtGui.QPixmap('share/flatcam_icon32.png')
        self.icon = QtWidgets.QLabel()
        self.icon.setPixmap(pixmap)
        self.title_box.addWidget(self.icon, stretch=0)

        ## Title label
        self.title_label = QtWidgets.QLabel("<font size=5><b>%s</b></font>" % _('Gerber Editor'))
        self.title_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.title_box.addWidget(self.title_label, stretch=1)

        ## Object name
        self.name_box = QtWidgets.QHBoxLayout()
        layout.addLayout(self.name_box)
        name_label = QtWidgets.QLabel(_("Name:"))
        self.name_box.addWidget(name_label)
        self.name_entry = FCEntry()
        self.name_box.addWidget(self.name_entry)

        ## Box for custom widgets
        # This gets populated in offspring implementations.
        self.custom_box = QtWidgets.QVBoxLayout()
        layout.addLayout(self.custom_box)

        # add a frame and inside add a vertical box layout. Inside this vbox layout I add all the Drills widgets
        # this way I can hide/show the frame
        self.apertures_frame = QtWidgets.QFrame()
        self.apertures_frame.setContentsMargins(0, 0, 0, 0)
        self.custom_box.addWidget(self.apertures_frame)
        self.apertures_box = QtWidgets.QVBoxLayout()
        self.apertures_box.setContentsMargins(0, 0, 0, 0)
        self.apertures_frame.setLayout(self.apertures_box)

        #### Gerber Apertures ####
        self.apertures_table_label = QtWidgets.QLabel(_('<b>Apertures:</b>'))
        self.apertures_table_label.setToolTip(
            _("Apertures Table for the Gerber Object.")
        )
        self.apertures_box.addWidget(self.apertures_table_label)

        self.apertures_table = FCTable()
        # delegate = SpinBoxDelegate(units=self.units)
        # self.apertures_table.setItemDelegateForColumn(1, delegate)

        self.apertures_box.addWidget(self.apertures_table)

        self.apertures_table.setColumnCount(5)
        self.apertures_table.setHorizontalHeaderLabels(['#', _('Code'), _('Type'), _('Size'), _('Dim')])
        self.apertures_table.setSortingEnabled(False)

        self.apertures_table.horizontalHeaderItem(0).setToolTip(
            _("Index"))
        self.apertures_table.horizontalHeaderItem(1).setToolTip(
            _("Aperture Code"))
        self.apertures_table.horizontalHeaderItem(2).setToolTip(
            _("Type of aperture: circular, rectangle, macros etc"))
        self.apertures_table.horizontalHeaderItem(4).setToolTip(
            _("Aperture Size:"))
        self.apertures_table.horizontalHeaderItem(4).setToolTip(
            _("Aperture Dimensions:\n"
              " - (width, height) for R, O type.\n"
              " - (dia, nVertices) for P type"))

        self.empty_label = QtWidgets.QLabel('')
        self.apertures_box.addWidget(self.empty_label)

        #### Add a new Tool ####
        self.addaperture_label = QtWidgets.QLabel('<b>%s</b>' % _('Add/Delete Aperture'))
        self.addaperture_label.setToolTip(
            _("Add/Delete an aperture to the aperture list")
        )
        self.apertures_box.addWidget(self.addaperture_label)

        grid1 = QtWidgets.QGridLayout()
        self.apertures_box.addLayout(grid1)

        addaperture_entry_lbl = QtWidgets.QLabel(_('Aperture Size:'))
        addaperture_entry_lbl.setToolTip(
        _("Size for the new aperture")
        )
        grid1.addWidget(addaperture_entry_lbl, 0, 0)

        hlay = QtWidgets.QHBoxLayout()
        self.addtool_entry = FCEntry()
        self.addtool_entry.setValidator(QtGui.QDoubleValidator(0.0001, 99.9999, 4))
        hlay.addWidget(self.addtool_entry)

        self.addaperture_btn = QtWidgets.QPushButton(_('Add Aperture'))
        self.addaperture_btn.setToolTip(
           _( "Add a new aperture to the aperture list")
        )
        self.addaperture_btn.setFixedWidth(80)
        hlay.addWidget(self.addaperture_btn)
        grid1.addLayout(hlay, 0, 1)

        grid2 = QtWidgets.QGridLayout()
        self.apertures_box.addLayout(grid2)

        self.delaperture_btn = QtWidgets.QPushButton(_('Delete Aperture'))
        self.delaperture_btn.setToolTip(
           _( "Delete a aperture in the aperture list")
        )
        grid2.addWidget(self.delaperture_btn, 0, 1)

        # add a frame and inside add a vertical box layout. Inside this vbox layout I add all the aperture widgets
        # this way I can hide/show the frame
        self.resize_frame = QtWidgets.QFrame()
        self.resize_frame.setContentsMargins(0, 0, 0, 0)
        self.apertures_box.addWidget(self.resize_frame)
        self.resize_box = QtWidgets.QVBoxLayout()
        self.resize_box.setContentsMargins(0, 0, 0, 0)
        self.resize_frame.setLayout(self.resize_box)

        #### Resize a aperture ####
        self.emptyresize_label = QtWidgets.QLabel('')
        self.resize_box.addWidget(self.emptyresize_label)

        self.apertureresize_label = QtWidgets.QLabel('<b>%s</b>' % _("Resize Aperture"))
        self.apertureresize_label.setToolTip(
            _("Resize a aperture or a selection of apertures.")
        )
        self.resize_box.addWidget(self.apertureresize_label)

        grid3 = QtWidgets.QGridLayout()
        self.resize_box.addLayout(grid3)

        res_entry_lbl = QtWidgets.QLabel(_('Resize Dia:'))
        res_entry_lbl.setToolTip(
           _( "Size to resize to.")
        )
        grid3.addWidget(res_entry_lbl, 0, 0)

        hlay2 = QtWidgets.QHBoxLayout()
        self.resdrill_entry = LengthEntry()
        hlay2.addWidget(self.resdrill_entry)

        self.resize_btn = QtWidgets.QPushButton(_('Resize'))
        self.resize_btn.setToolTip(
            _("Resize drill(s)")
        )
        self.resize_btn.setFixedWidth(80)
        hlay2.addWidget(self.resize_btn)
        grid3.addLayout(hlay2, 0, 1)

        self.resize_frame.hide()

        # add a frame and inside add a vertical box layout. Inside this vbox layout I add
        # all the add drill array  widgets
        # this way I can hide/show the frame
        self.array_frame = QtWidgets.QFrame()
        self.array_frame.setContentsMargins(0, 0, 0, 0)
        self.apertures_box.addWidget(self.array_frame)
        self.array_box = QtWidgets.QVBoxLayout()
        self.array_box.setContentsMargins(0, 0, 0, 0)
        self.array_frame.setLayout(self.array_box)

        #### Add DRILL Array ####
        self.emptyarray_label = QtWidgets.QLabel('')
        self.array_box.addWidget(self.emptyarray_label)

        self.drillarray_label = QtWidgets.QLabel('<b>%s</b>' % _("Add Drill Array"))
        self.drillarray_label.setToolTip(
            _("Add an array of drills (linear or circular array)")
        )
        self.array_box.addWidget(self.drillarray_label)

        self.array_type_combo = FCComboBox()
        self.array_type_combo.setToolTip(
           _( "Select the type of drills array to create.\n"
            "It can be Linear X(Y) or Circular")
        )
        self.array_type_combo.addItem(_("Linear"))
        self.array_type_combo.addItem(_("Circular"))

        self.array_box.addWidget(self.array_type_combo)

        self.array_form = QtWidgets.QFormLayout()
        self.array_box.addLayout(self.array_form)

        self.drill_array_size_label = QtWidgets.QLabel(_('Nr of drills:'))
        self.drill_array_size_label.setToolTip(
            _("Specify how many drills to be in the array.")
        )
        self.drill_array_size_label.setFixedWidth(100)

        self.drill_array_size_entry = LengthEntry()
        self.array_form.addRow(self.drill_array_size_label, self.drill_array_size_entry)

        self.array_linear_frame = QtWidgets.QFrame()
        self.array_linear_frame.setContentsMargins(0, 0, 0, 0)
        self.array_box.addWidget(self.array_linear_frame)
        self.linear_box = QtWidgets.QVBoxLayout()
        self.linear_box.setContentsMargins(0, 0, 0, 0)
        self.array_linear_frame.setLayout(self.linear_box)

        self.linear_form = QtWidgets.QFormLayout()
        self.linear_box.addLayout(self.linear_form)

        self.drill_axis_label = QtWidgets.QLabel(_('Direction:'))
        self.drill_axis_label.setToolTip(
            _("Direction on which the linear array is oriented:\n"
            "- 'X' - horizontal axis \n"
            "- 'Y' - vertical axis or \n"
            "- 'Angle' - a custom angle for the array inclination")
        )
        self.drill_axis_label.setFixedWidth(100)

        self.drill_axis_radio = RadioSet([{'label': 'X', 'value': 'X'},
                                          {'label': 'Y', 'value': 'Y'},
                                          {'label': _('Angle'), 'value': 'A'}])
        self.drill_axis_radio.set_value('X')
        self.linear_form.addRow(self.drill_axis_label, self.drill_axis_radio)

        self.drill_pitch_label = QtWidgets.QLabel(_('Pitch:'))
        self.drill_pitch_label.setToolTip(
            _("Pitch = Distance between elements of the array.")
        )
        self.drill_pitch_label.setFixedWidth(100)

        self.drill_pitch_entry = LengthEntry()
        self.linear_form.addRow(self.drill_pitch_label, self.drill_pitch_entry)

        self.linear_angle_label = QtWidgets.QLabel(_('Angle:'))
        self.linear_angle_label.setToolTip(
           _( "Angle at which the linear array is placed.\n"
            "The precision is of max 2 decimals.\n"
            "Min value is: -359.99 degrees.\n"
            "Max value is:  360.00 degrees.")
        )
        self.linear_angle_label.setFixedWidth(100)

        self.linear_angle_spinner = FCDoubleSpinner()
        self.linear_angle_spinner.set_precision(2)
        self.linear_angle_spinner.setRange(-359.99, 360.00)
        self.linear_form.addRow(self.linear_angle_label, self.linear_angle_spinner)

        self.array_circular_frame = QtWidgets.QFrame()
        self.array_circular_frame.setContentsMargins(0, 0, 0, 0)
        self.array_box.addWidget(self.array_circular_frame)
        self.circular_box = QtWidgets.QVBoxLayout()
        self.circular_box.setContentsMargins(0, 0, 0, 0)
        self.array_circular_frame.setLayout(self.circular_box)

        self.drill_direction_label = QtWidgets.QLabel(_('Direction:'))
        self.drill_direction_label.setToolTip(
           _( "Direction for circular array."
            "Can be CW = clockwise or CCW = counter clockwise.")
        )
        self.drill_direction_label.setFixedWidth(100)

        self.circular_form = QtWidgets.QFormLayout()
        self.circular_box.addLayout(self.circular_form)

        self.drill_direction_radio = RadioSet([{'label': 'CW', 'value': 'CW'},
                                               {'label': 'CCW.', 'value': 'CCW'}])
        self.drill_direction_radio.set_value('CW')
        self.circular_form.addRow(self.drill_direction_label, self.drill_direction_radio)

        self.drill_angle_label = QtWidgets.QLabel(_('Angle:'))
        self.drill_angle_label.setToolTip(
            _("Angle at which each element in circular array is placed.")
        )
        self.drill_angle_label.setFixedWidth(100)

        self.drill_angle_entry = LengthEntry()
        self.circular_form.addRow(self.drill_angle_label, self.drill_angle_entry)

        self.array_circular_frame.hide()

        self.linear_angle_spinner.hide()
        self.linear_angle_label.hide()

        self.array_frame.hide()
        self.apertures_box.addStretch()

        ## Toolbar events and properties
        self.tools_gerber = {
            "select": {"button": self.app.ui.select_drill_btn,
                       "constructor": FCApertureSelect},
            "drill_resize": {"button": self.app.ui.resize_drill_btn,
                       "constructor": FCApertureResize},
            "drill_copy": {"button": self.app.ui.copy_drill_btn,
                     "constructor": FCApertureCopy},
            "drill_move": {"button": self.app.ui.move_drill_btn,
                     "constructor": FCApertureMove},
        }

        ### Data
        self.active_tool = None

        self.storage_dict = {}
        self.current_storage = []

        # build the data from the Excellon point into a dictionary
        #  {tool_dia: [geometry_in_points]}
        self.points_edit = {}
        self.sorted_apid =[]

        self.new_apertures = {}
        self.new_aperture_macros = {}

        # dictionary to store the tool_row and diameters in Tool_table
        # it will be updated everytime self.build_ui() is called
        self.olddia_newdia = {}

        self.tool2tooldia = {}

        # this will store the value for the last selected tool, for use after clicking on canvas when the selection
        # is cleared but as a side effect also the selected tool is cleared
        self.last_aperture_selected = None
        self.utility = []

        # this will flag if the Editor "tools" are launched from key shortcuts (True) or from menu toolbar (False)
        self.launched_from_shortcuts = False

        # this var will store the state of the toolbar before starting the editor
        self.toolbar_old_state = False

        self.app.ui.delete_drill_btn.triggered.connect(self.on_delete_btn)
        self.name_entry.returnPressed.connect(self.on_name_activate)
        self.addaperture_btn.clicked.connect(self.on_tool_add)
        # self.addtool_entry.editingFinished.connect(self.on_tool_add)
        self.delaperture_btn.clicked.connect(self.on_tool_delete)
        self.apertures_table.selectionModel().currentChanged.connect(self.on_row_selected)
        self.array_type_combo.currentIndexChanged.connect(self.on_array_type_combo)

        self.drill_axis_radio.activated_custom.connect(self.on_linear_angle_radio)


        self.app.ui.exc_resize_drill_menuitem.triggered.connect(self.exc_resize_drills)
        self.app.ui.exc_copy_drill_menuitem.triggered.connect(self.exc_copy_drills)
        self.app.ui.exc_delete_drill_menuitem.triggered.connect(self.on_delete_btn)

        self.app.ui.exc_move_drill_menuitem.triggered.connect(self.exc_move_drills)


        # Init GUI
        self.drill_array_size_entry.set_value(5)
        self.drill_pitch_entry.set_value(2.54)
        self.drill_angle_entry.set_value(12)
        self.drill_direction_radio.set_value('CW')
        self.drill_axis_radio.set_value('X')
        self.gerber_obj = None

        # VisPy Visuals
        self.shapes = self.app.plotcanvas.new_shape_collection(layers=1)
        self.tool_shape = self.app.plotcanvas.new_shape_collection(layers=1)
        self.app.pool_recreated.connect(self.pool_recreated)

        # Remove from scene
        self.shapes.enabled = False
        self.tool_shape.enabled = False

        ## List of selected shapes.
        self.selected = []

        self.move_timer = QtCore.QTimer()
        self.move_timer.setSingleShot(True)

        self.key = None  # Currently pressed key
        self.modifiers = None
        self.x = None  # Current mouse cursor pos
        self.y = None
        # Current snapped mouse pos
        self.snap_x = None
        self.snap_y = None
        self.pos = None

        def make_callback(thetool):
            def f():
                self.on_tool_select(thetool)
            return f

        for tool in self.tools_gerber:
            self.tools_gerber[tool]["button"].triggered.connect(make_callback(tool))  # Events
            self.tools_gerber[tool]["button"].setCheckable(True)  # Checkable

        self.options = {
            "global_gridx": 0.1,
            "global_gridy": 0.1,
            "snap_max": 0.05,
            "grid_snap": True,
            "corner_snap": False,
            "grid_gap_link": True
        }
        self.app.options_read_form()

        for option in self.options:
            if option in self.app.options:
                self.options[option] = self.app.options[option]

        # flag to show if the object was modified
        self.is_modified = False

        self.edited_obj_name = ""

        self.tool_row = 0

        # store the status of the editor so the Delete at object level will not work until the edit is finished
        self.editor_active = False

        def entry2option(option, entry):
            self.options[option] = float(entry.text())

        # store the status of the editor so the Delete at object level will not work until the edit is finished
        self.editor_active = False

    def pool_recreated(self, pool):
        self.shapes.pool = pool
        self.tool_shape.pool = pool

    def set_ui(self):
        # updated units
        self.units = self.app.ui.general_defaults_form.general_app_group.units_radio.get_value().upper()

        self.olddia_newdia.clear()
        self.tool2tooldia.clear()

        # update the olddia_newdia dict to make sure we have an updated state of the tool_table
        for key in self.storage_dict:
            self.olddia_newdia[key] = key

        sort_temp = []
        for aperture in self.olddia_newdia:
            sort_temp.append(float(aperture))
        self.sorted_apid = sorted(sort_temp)

        # populate self.intial_table_rows dict with the tool number as keys and tool diameters as values
        for i in range(len(self.sorted_apid)):
            tt_aperture = self.sorted_apid[i]
            self.tool2tooldia[i + 1] = tt_aperture

    def build_ui(self):

        try:
            # if connected, disconnect the signal from the slot on item_changed as it creates issues
            self.apertures_table.itemChanged.disconnect()
        except:
            pass

        # updated units
        self.units = self.app.ui.general_defaults_form.general_app_group.units_radio.get_value().upper()

        # make a new name for the new Excellon object (the one with edited content)
        self.edited_obj_name = self.gerber_obj.options['name']
        self.name_entry.set_value(self.edited_obj_name)

        if self.units == "IN":
            self.addtool_entry.set_value(0.039)
        else:
            self.addtool_entry.set_value(1.00)

        self.apertures_row = 0
        aper_no = self.apertures_row + 1
        sort = []
        for k, v in list(self.gerber_obj.apertures.items()):
            sort.append(int(k))
        sorted_apertures = sorted(sort)

        sort = []
        for k, v in list(self.gerber_obj.aperture_macros.items()):
            sort.append(k)
        sorted_macros = sorted(sort)

        n = len(sorted_apertures) + len(sorted_macros)
        self.apertures_table.setRowCount(n)

        for ap_code in sorted_apertures:
            ap_code = str(ap_code)

            ap_id_item = QtWidgets.QTableWidgetItem('%d' % int(self.apertures_row + 1))
            ap_id_item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
            self.apertures_table.setItem(self.apertures_row, 0, ap_id_item)  # Tool name/id

            ap_code_item = QtWidgets.QTableWidgetItem(ap_code)
            ap_code_item.setFlags(QtCore.Qt.ItemIsEnabled)

            ap_type_item = QtWidgets.QTableWidgetItem(str(self.gerber_obj.apertures[ap_code]['type']))
            ap_type_item.setFlags(QtCore.Qt.ItemIsEnabled)

            if str(self.gerber_obj.apertures[ap_code]['type']) == 'R' or str(self.gerber_obj.apertures[ap_code]['type']) == 'O':
                ap_dim_item = QtWidgets.QTableWidgetItem(
                    '%.4f, %.4f' % (self.gerber_obj.apertures[ap_code]['width'] * self.gerber_obj.file_units_factor,
                                    self.gerber_obj.apertures[ap_code]['height'] * self.gerber_obj.file_units_factor
                                    )
                )
                ap_dim_item.setFlags(QtCore.Qt.ItemIsEnabled)
            elif str(self.gerber_obj.apertures[ap_code]['type']) == 'P':
                ap_dim_item = QtWidgets.QTableWidgetItem(
                    '%.4f, %.4f' % (self.gerber_obj.apertures[ap_code]['diam'] * self.gerber_obj.file_units_factor,
                                    self.gerber_obj.apertures[ap_code]['nVertices'] * self.gerber_obj.file_units_factor)
                )
                ap_dim_item.setFlags(QtCore.Qt.ItemIsEnabled)
            else:
                ap_dim_item = QtWidgets.QTableWidgetItem('')
                ap_dim_item.setFlags(QtCore.Qt.ItemIsEnabled)

            try:
                if self.gerber_obj.apertures[ap_code]['size'] is not None:
                    ap_size_item = QtWidgets.QTableWidgetItem('%.4f' %
                                                              float(self.gerber_obj.apertures[ap_code]['size'] *
                                                                    self.gerber_obj.file_units_factor))
                else:
                    ap_size_item = QtWidgets.QTableWidgetItem('')
            except KeyError:
                ap_size_item = QtWidgets.QTableWidgetItem('')
            ap_size_item.setFlags(QtCore.Qt.ItemIsEnabled)

            self.apertures_table.setItem(self.apertures_row, 1, ap_code_item)  # Aperture Code
            self.apertures_table.setItem(self.apertures_row, 2, ap_type_item)  # Aperture Type
            self.apertures_table.setItem(self.apertures_row, 3, ap_size_item)  # Aperture Dimensions
            self.apertures_table.setItem(self.apertures_row, 4, ap_dim_item)  # Aperture Dimensions

            self.apertures_row += 1

        for ap_code in sorted_macros:
            ap_code = str(ap_code)

            ap_id_item = QtWidgets.QTableWidgetItem('%d' % int(self.apertures_row + 1))
            ap_id_item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
            self.apertures_table.setItem(self.apertures_row, 0, ap_id_item)  # Tool name/id

            ap_code_item = QtWidgets.QTableWidgetItem(ap_code)

            ap_type_item = QtWidgets.QTableWidgetItem('AM')
            ap_type_item.setFlags(QtCore.Qt.ItemIsEnabled)

            self.apertures_table.setItem(self.apertures_row, 1, ap_code_item)  # Aperture Code
            self.apertures_table.setItem(self.apertures_row, 2, ap_type_item)  # Aperture Type

            self.apertures_row += 1

        self.apertures_table.selectColumn(0)
        self.apertures_table.resizeColumnsToContents()
        self.apertures_table.resizeRowsToContents()

        vertical_header = self.apertures_table.verticalHeader()
        # vertical_header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        vertical_header.hide()
        self.apertures_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        horizontal_header = self.apertures_table.horizontalHeader()
        horizontal_header.setMinimumSectionSize(10)
        horizontal_header.setDefaultSectionSize(70)
        horizontal_header.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        horizontal_header.resizeSection(0, 20)
        horizontal_header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        horizontal_header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        horizontal_header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        horizontal_header.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)

        self.apertures_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.apertures_table.setSortingEnabled(False)
        self.apertures_table.setMinimumHeight(self.apertures_table.getHeight())
        self.apertures_table.setMaximumHeight(self.apertures_table.getHeight())

        # make sure no rows are selected so the user have to click the correct row, meaning selecting the correct tool
        self.apertures_table.clearSelection()

        # Remove anything else in the GUI Selected Tab
        self.app.ui.selected_scroll_area.takeWidget()
        # Put ourself in the GUI Selected Tab
        self.app.ui.selected_scroll_area.setWidget(self.grb_edit_widget)
        # Switch notebook to Selected page
        self.app.ui.notebook.setCurrentWidget(self.app.ui.selected_tab)

        # we reactivate the signals after the after the tool adding as we don't need to see the tool been populated
        self.apertures_table.itemChanged.connect(self.on_tool_edit)

    def on_tool_add(self, tooldia=None):
        self.is_modified = True
        if tooldia:
            tool_dia = tooldia
        else:
            try:
                tool_dia = float(self.addtool_entry.get_value())
            except ValueError:
                # try to convert comma to decimal point. if it's still not working error message and return
                try:
                    tool_dia = float(self.addtool_entry.get_value().replace(',', '.'))
                except ValueError:
                    self.app.inform.emit(_("[ERROR_NOTCL] Wrong value format entered, "
                                         "use a number.")
                                         )
                    return

        if tool_dia not in self.olddia_newdia:
            storage_elem = FlatCAMGeoEditor.make_storage()
            self.storage_dict[tool_dia] = storage_elem

            # self.olddia_newdia dict keeps the evidence on current tools diameters as keys and gets updated on values
            # each time a tool diameter is edited or added
            self.olddia_newdia[tool_dia] = tool_dia
        else:
            self.app.inform.emit(_("[WARNING_NOTCL] Tool already in the original or actual tool list.\n"
                                 "Save and reedit Excellon if you need to add this tool. ")
                                 )
            return

        # since we add a new tool, we update also the initial state of the tool_table through it's dictionary
        # we add a new entry in the tool2tooldia dict
        self.tool2tooldia[len(self.olddia_newdia)] = tool_dia

        self.app.inform.emit(_("[success] Added new tool with dia: {dia} {units}").format(dia=str(tool_dia), units=str(self.units)))

        self.build_ui()

        # make a quick sort through the tool2tooldia dict so we find which row to select
        row_to_be_selected = None
        for key in sorted(self.tool2tooldia):
            if self.tool2tooldia[key] == tool_dia:
                row_to_be_selected = int(key) - 1
                break

        self.apertures_table.selectRow(row_to_be_selected)

    def on_tool_delete(self, dia=None):
        self.is_modified = True
        deleted_tool_dia_list = []
        deleted_tool_offset_list = []

        try:
            if dia is None or dia is False:
                # deleted_tool_dia = float(self.apertures_table.item(self.apertures_table.currentRow(), 1).text())
                for index in self.apertures_table.selectionModel().selectedRows():
                    row = index.row()
                    deleted_tool_dia_list.append(float(self.apertures_table.item(row, 1).text()))
            else:
                if isinstance(dia, list):
                    for dd in dia:
                        deleted_tool_dia_list.append(float('%.4f' % dd))
                else:
                    deleted_tool_dia_list.append(float('%.4f' % dia))
        except:
            self.app.inform.emit(_("[WARNING_NOTCL] Select a tool in Tool Table"))
            return

        for deleted_tool_dia in deleted_tool_dia_list:

            # delete de tool offset
            self.gerber_obj.tool_offset.pop(float(deleted_tool_dia), None)

            # delete the storage used for that tool
            storage_elem = FlatCAMGeoEditor.make_storage()
            self.storage_dict[deleted_tool_dia] = storage_elem
            self.storage_dict.pop(deleted_tool_dia, None)

            # I've added this flag_del variable because dictionary don't like
            # having keys deleted while iterating through them
            flag_del = []
            # self.points_edit.pop(deleted_tool_dia, None)
            for deleted_tool in self.tool2tooldia:
                if self.tool2tooldia[deleted_tool] == deleted_tool_dia:
                    flag_del.append(deleted_tool)

            if flag_del:
                for tool_to_be_deleted in flag_del:
                    # delete the tool
                    self.tool2tooldia.pop(tool_to_be_deleted, None)

                    # delete also the drills from points_edit dict just in case we add the tool again, we don't want to show the
                    # number of drills from before was deleter
                    self.points_edit[deleted_tool_dia] = []
                flag_del = []

            self.olddia_newdia.pop(deleted_tool_dia, None)

            self.app.inform.emit(_("[success] Deleted tool with dia: {del_dia} {units}").format(del_dia=str(deleted_tool_dia), units=str(self.units)))

        self.replot()
        # self.app.inform.emit("Could not delete selected tool")

        self.build_ui()

    def on_tool_edit(self, item_changed):

        # if connected, disconnect the signal from the slot on item_changed as it creates issues
        self.apertures_table.itemChanged.disconnect()
        # self.apertures_table.selectionModel().currentChanged.disconnect()

        self.is_modified = True
        geometry = []
        current_table_dia_edited = None

        if self.apertures_table.currentItem() is not None:
            try:
                current_table_dia_edited = float(self.apertures_table.currentItem().text())
            except ValueError as e:
                log.debug("FlatCAMExcEditor.on_tool_edit() --> %s" % str(e))
                self.apertures_table.setCurrentItem(None)
                return

        row_of_item_changed = self.apertures_table.currentRow()

        # rows start with 0, tools start with 1 so we adjust the value by 1
        key_in_tool2tooldia = row_of_item_changed + 1

        dia_changed = self.tool2tooldia[key_in_tool2tooldia]

        # tool diameter is not used so we create a new tool with the desired diameter
        if current_table_dia_edited not in self.olddia_newdia.values():
            # update the dict that holds as keys our initial diameters and as values the edited diameters
            self.olddia_newdia[dia_changed] = current_table_dia_edited
            # update the dict that holds tool_no as key and tool_dia as value
            self.tool2tooldia[key_in_tool2tooldia] = current_table_dia_edited

            # update the tool offset
            modified_offset = self.gerber_obj.tool_offset.pop(dia_changed)
            self.gerber_obj.tool_offset[current_table_dia_edited] = modified_offset

            self.replot()
        else:
            # tool diameter is already in use so we move the drills from the prior tool to the new tool
            factor = current_table_dia_edited / dia_changed
            for shape in self.storage_dict[dia_changed].get_objects():
                geometry.append(DrawToolShape(
                    MultiLineString([affinity.scale(subgeo, xfact=factor, yfact=factor) for subgeo in shape.geo])))

                self.points_edit[current_table_dia_edited].append((0, 0))
            self.add_gerber_shape(geometry, self.storage_dict[current_table_dia_edited])

            self.on_tool_delete(dia=dia_changed)

            # delete the tool offset
            self.gerber_obj.tool_offset.pop(dia_changed, None)

        # we reactivate the signals after the after the tool editing
        self.apertures_table.itemChanged.connect(self.on_tool_edit)
        # self.apertures_table.selectionModel().currentChanged.connect(self.on_row_selected)

    def on_name_activate(self):
        self.edited_obj_name = self.name_entry.get_value()

    def activate(self):
        self.connect_canvas_event_handlers()

        # self.app.collection.view.keyPressed.connect(self.on_canvas_key)

        self.shapes.enabled = True
        self.tool_shape.enabled = True
        # self.app.app_cursor.enabled = True

        self.app.ui.snap_max_dist_entry.setEnabled(True)
        self.app.ui.corner_snap_btn.setEnabled(True)
        self.app.ui.snap_magnet.setVisible(True)
        self.app.ui.corner_snap_btn.setVisible(True)

        self.app.ui.grb_editor_menu.setDisabled(False)
        self.app.ui.grb_editor_menu.menuAction().setVisible(True)

        self.app.ui.update_obj_btn.setEnabled(True)
        self.app.ui.grb_editor_cmenu.setEnabled(True)

        self.app.ui.grb_edit_toolbar.setDisabled(False)
        self.app.ui.grb_edit_toolbar.setVisible(True)
        # self.app.ui.snap_toolbar.setDisabled(False)

        # start with GRID toolbar activated
        if self.app.ui.grid_snap_btn.isChecked() is False:
            self.app.ui.grid_snap_btn.trigger()

        # Tell the App that the editor is active
        self.editor_active = True

    def deactivate(self):
        self.disconnect_canvas_event_handlers()
        self.clear()
        self.app.ui.grb_edit_toolbar.setDisabled(True)

        settings = QSettings("Open Source", "FlatCAM")
        if settings.contains("layout"):
            layout = settings.value('layout', type=str)
            if layout == 'standard':
                # self.app.ui.exc_edit_toolbar.setVisible(False)

                self.app.ui.snap_max_dist_entry.setEnabled(False)
                self.app.ui.corner_snap_btn.setEnabled(False)
                self.app.ui.snap_magnet.setVisible(False)
                self.app.ui.corner_snap_btn.setVisible(False)
            elif layout == 'compact':
                # self.app.ui.exc_edit_toolbar.setVisible(True)

                self.app.ui.snap_max_dist_entry.setEnabled(False)
                self.app.ui.corner_snap_btn.setEnabled(False)
                self.app.ui.snap_magnet.setVisible(True)
                self.app.ui.corner_snap_btn.setVisible(True)
        else:
            # self.app.ui.exc_edit_toolbar.setVisible(False)

            self.app.ui.snap_max_dist_entry.setEnabled(False)
            self.app.ui.corner_snap_btn.setEnabled(False)
            self.app.ui.snap_magnet.setVisible(False)
            self.app.ui.corner_snap_btn.setVisible(False)

        # set the Editor Toolbar visibility to what was before entering in the Editor
        self.app.ui.grb_edit_toolbar.setVisible(False) if self.toolbar_old_state is False \
            else self.app.ui.grb_edit_toolbar.setVisible(True)

        # Disable visuals
        self.shapes.enabled = False
        self.tool_shape.enabled = False
        # self.app.app_cursor.enabled = False

        # Tell the app that the editor is no longer active
        self.editor_active = False

        self.app.ui.grb_editor_menu.setDisabled(True)
        self.app.ui.grb_editor_menu.menuAction().setVisible(False)

        self.app.ui.update_obj_btn.setEnabled(False)

        self.app.ui.g_editor_cmenu.setEnabled(False)
        self.app.ui.grb_editor_cmenu.setEnabled(False)
        self.app.ui.e_editor_cmenu.setEnabled(False)

        # Show original geometry
        if self.gerber_obj:
            self.gerber_obj.visible = True

    def connect_canvas_event_handlers(self):
        ## Canvas events

        # make sure that the shortcuts key and mouse events will no longer be linked to the methods from FlatCAMApp
        # but those from FlatCAMGeoEditor

        self.app.plotcanvas.vis_disconnect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.vis_disconnect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.vis_disconnect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.app.plotcanvas.vis_disconnect('mouse_double_click', self.app.on_double_click_over_plot)
        self.app.collection.view.clicked.disconnect()

        self.canvas.vis_connect('mouse_press', self.on_canvas_click)
        self.canvas.vis_connect('mouse_move', self.on_canvas_move)
        self.canvas.vis_connect('mouse_release', self.on_canvas_click_release)

    def disconnect_canvas_event_handlers(self):
        self.canvas.vis_disconnect('mouse_press', self.on_canvas_click)
        self.canvas.vis_disconnect('mouse_move', self.on_canvas_move)
        self.canvas.vis_disconnect('mouse_release', self.on_canvas_click_release)

        # we restore the key and mouse control to FlatCAMApp method
        self.app.plotcanvas.vis_connect('mouse_press', self.app.on_mouse_click_over_plot)
        self.app.plotcanvas.vis_connect('mouse_move', self.app.on_mouse_move_over_plot)
        self.app.plotcanvas.vis_connect('mouse_release', self.app.on_mouse_click_release_over_plot)
        self.app.plotcanvas.vis_connect('mouse_double_click', self.app.on_double_click_over_plot)
        self.app.collection.view.clicked.connect(self.app.collection.on_mouse_down)

    def clear(self):
        self.active_tool = None
        # self.shape_buffer = []
        self.selected = []

        self.storage_dict = {}

        self.shapes.clear(update=True)
        self.tool_shape.clear(update=True)

        # self.storage = FlatCAMExcEditor.make_storage()
        self.replot()

    def edit_fcgerber(self, exc_obj):
        """
        Imports the geometry found in self.apertures from the given FlatCAM Gerber object
        into the editor.

        :param fcgeometry: FlatCAMExcellon
        :return: None
        """

        assert isinstance(exc_obj, Gerber), \
            "Expected an Excellon Object, got %s" % type(exc_obj)

        self.deactivate()
        self.activate()

        # Hide original geometry
        self.gerber_obj = exc_obj
        exc_obj.visible = False

        # Set selection tolerance
        # DrawToolShape.tolerance = fc_excellon.drawing_tolerance * 10

        self.select_tool("select")

        self.set_ui()

        # now that we hava data, create the GUI interface and add it to the Tool Tab
        self.build_ui()

        # we activate this after the initial build as we don't need to see the tool been populated
        self.apertures_table.itemChanged.connect(self.on_tool_edit)

        # build the geometry for each tool-diameter, each drill will be represented by a '+' symbol
        # and then add it to the storage elements (each storage elements is a member of a list

        def job_thread(apid):
            with self.app.proc_container.new(_("Adding aperture: %s geo ...") % str(apid)):
                solid_storage_elem = []
                follow_storage_elem = []

                self.storage_dict[apid] = {}
                for k, v in self.gerber_obj.apertures[apid].items():
                    if k == 'solid_geometry':
                        for geo in v:
                            if geo is not None:
                                self.add_gerber_shape(DrawToolShape(geo), solid_storage_elem)
                        self.storage_dict[apid][k] = solid_storage_elem
                    elif k == 'follow_geometry':
                        for geo in v:
                            if geo is not None:
                                self.add_gerber_shape(DrawToolShape(geo), follow_storage_elem)
                        self.storage_dict[apid][k] = follow_storage_elem
                    else:
                        self.storage_dict[apid][k] = v

                # Check promises and clear if exists
                self.app.collection.plot_remove_promise(apid)

        for apid in self.gerber_obj.apertures:
            self.app.worker_task.emit({'fcn': job_thread, 'params': [apid]})
            self.app.collection.plot_promise(apid)

        self.start_delayed_plot(check_period=500)

    def update_fcgerber(self, grb_obj):
        """
        Create a new Gerber object that contain the edited content of the source Gerber object

        :param grb_obj: FlatCAMGerber
        :return: None
        """

        if "_edit" in self.edited_obj_name:
            try:
                id = int(self.edited_obj_name[-1]) + 1
                self.edited_obj_name = self.edited_obj_name[:-1] + str(id)
            except ValueError:
                self.edited_obj_name += "_1"
        else:
            self.edited_obj_name += "_edit"

        self.app.worker_task.emit({'fcn': self.new_edited_gerber,
                                   'params': [self.edited_obj_name]})

        # reset the tool table
        self.apertures_table.clear()
        self.apertures_table.setHorizontalHeaderLabels(['#', _('Code'), _('Type'), _('Size'), _('Dim')])
        self.last_aperture_selected = None

        # restore GUI to the Selected TAB
        # Remove anything else in the GUI
        self.app.ui.tool_scroll_area.takeWidget()
        # Switch notebook to Selected page
        self.app.ui.notebook.setCurrentWidget(self.app.ui.selected_tab)

    def update_options(self, obj):
        try:
            if not obj.options:
                obj.options = {}
                obj.options['xmin'] = 0
                obj.options['ymin'] = 0
                obj.options['xmax'] = 0
                obj.options['ymax'] = 0
                return True
            else:
                return False
        except AttributeError:
            obj.options = {}
            return True

    def new_edited_gerber(self, outname):
        """
        Creates a new Gerber object for the edited Gerber. Thread-safe.

        :param outname: Name of the resulting object. None causes the name to be that of the file.
        :type outname: str
        :return: None
        """

        self.app.log.debug("Update the Gerber object with edited content. Source is %s" %
                           self.gerber_obj.options['name'])

        # How the object should be initialized
        def obj_init(grb_obj, app_obj):
            poly_buffer = []
            follow_buffer = []

            for storage_apid, storage_val in self.storage_dict.items():
                grb_obj.apertures[storage_apid] = {}
                for k, v in storage_val.items():
                    if k == 'solid_geometry':
                        grb_obj.apertures[storage_apid][k] = []
                        for geo in v:
                            grb_obj.apertures[storage_apid][k].append(deepcopy(geo.geo))
                            poly_buffer.append(deepcopy(geo.geo))
                    if k == 'follow_geometry':
                        grb_obj.apertures[storage_apid][k] = []
                        for geo in v:
                            grb_obj.apertures[storage_apid][k].append(deepcopy(geo.geo))
                            follow_buffer.append(deepcopy(geo.geo))
                    else:
                        grb_obj.apertures[storage_apid][k] = v

            grb_obj.aperture_macros = deepcopy(self.gerber_obj.aperture_macros)

            new_poly = MultiPolygon(poly_buffer)
            new_poly = new_poly.buffer(0.00000001)
            new_poly = new_poly.buffer(-0.00000001)
            grb_obj.solid_geometry = new_poly

            grb_obj.follow_geometry = deepcopy(follow_buffer)

            grb_obj.options = self.gerber_obj.options.copy()
            grb_obj.options['name'] = outname


            try:
                grb_obj.create_geometry()
            except KeyError:
                self.app.inform.emit(
                   _( "[ERROR_NOTCL] There are no Aperture definitions in the file. Aborting Gerber creation.")
                )
            except:
                msg = _("[ERROR] An internal error has ocurred. See shell.\n")
                msg += traceback.format_exc()
                app_obj.inform.emit(msg)
                raise
                # raise

        with self.app.proc_container.new(_("Creating Gerber.")):
            try:
                self.app.new_object("gerber", outname, obj_init)
            except Exception as e:
                log.error("Error on object creation: %s" % str(e))
                self.app.progress.emit(100)
                return

            self.app.inform.emit(_("[success] Gerber editing finished."))
            # self.progress.emit(100)

    def on_tool_select(self, tool):
        """
        Behavior of the toolbar. Tool initialization.

        :rtype : None
        """
        current_tool = tool

        self.app.log.debug("on_tool_select('%s')" % tool)

        if self.last_aperture_selected is None and current_tool is not 'select':
            # self.draw_app.select_tool('select')
            self.complete = True
            current_tool = 'select'
            self.app.inform.emit(_("[WARNING_NOTCL] Cancelled. There is no Tool/Drill selected"))

        # This is to make the group behave as radio group
        if current_tool in self.tools_gerber:
            if self.tools_gerber[current_tool]["button"].isChecked():
                self.app.log.debug("%s is checked." % current_tool)
                for t in self.tools_gerber:
                    if t != current_tool:
                        self.tools_gerber[t]["button"].setChecked(False)

                # this is where the Editor toolbar classes (button's) are instantiated
                self.active_tool = self.tools_gerber[current_tool]["constructor"](self)
                # self.app.inform.emit(self.active_tool.start_msg)
            else:
                self.app.log.debug("%s is NOT checked." % current_tool)
                for t in self.tools_gerber:
                    self.tools_gerber[t]["button"].setChecked(False)
                self.active_tool = None

    def on_row_selected(self):
        self.selected = []

        try:
            selected_dia = self.tool2tooldia[self.apertures_table.currentRow() + 1]
            self.last_aperture_selected = self.apertures_table.currentRow() + 1
            for obj in self.storage_dict[selected_dia].get_objects():
                self.selected.append(obj)
        except Exception as e:
            self.app.log.debug(str(e))

        self.replot()

    def toolbar_tool_toggle(self, key):
        self.options[key] = self.sender().isChecked()
        if self.options[key] == True:
            return 1
        else:
            return 0

    def on_canvas_click(self, event):
        """
        event.x and .y have canvas coordinates
        event.xdaya and .ydata have plot coordinates

        :param event: Event object dispatched by Matplotlib
        :return: None
        """

        if event.button is 1:
            self.app.ui.rel_position_label.setText("<b>Dx</b>: %.4f&nbsp;&nbsp;  <b>Dy</b>: "
                                                   "%.4f&nbsp;&nbsp;&nbsp;&nbsp;" % (0, 0))
            self.pos = self.canvas.vispy_canvas.translate_coords(event.pos)

            ### Snap coordinates
            x, y = self.app.geo_editor.snap(self.pos[0], self.pos[1])

            self.pos = (x, y)
            # print(self.active_tool)

            # Selection with left mouse button
            if self.active_tool is not None and event.button is 1:
                # Dispatch event to active_tool
                # msg = self.active_tool.click(self.app.geo_editor.snap(event.xdata, event.ydata))
                msg = self.active_tool.click(self.app.geo_editor.snap(self.pos[0], self.pos[1]))

                # If it is a shape generating tool
                if isinstance(self.active_tool, FCShapeTool) and self.active_tool.complete:
                    if self.current_storage is not None:
                        self.on_exc_shape_complete(self.current_storage)
                        self.build_ui()
                    # MS: always return to the Select Tool if modifier key is not pressed
                    # else return to the current tool
                    key_modifier = QtWidgets.QApplication.keyboardModifiers()
                    if self.app.defaults["global_mselect_key"] == 'Control':
                        modifier_to_use = Qt.ControlModifier
                    else:
                        modifier_to_use = Qt.ShiftModifier
                    # if modifier key is pressed then we add to the selected list the current shape but if it's already
                    # in the selected list, we removed it. Therefore first click selects, second deselects.
                    if key_modifier == modifier_to_use:
                        self.select_tool(self.active_tool.name)
                    else:
                        self.select_tool("select")
                        return

                if isinstance(self.active_tool, FCApertureSelect):
                    # self.app.log.debug("Replotting after click.")
                    self.replot()
            else:
                self.app.log.debug("No active tool to respond to click!")

    def on_exc_shape_complete(self, storage):
        self.app.log.debug("on_shape_complete()")

        # Add shape
        if type(storage) is list:
            for item_storage in storage:
                self.add_gerber_shape(self.active_tool.geometry, item_storage)
        else:
            self.add_gerber_shape(self.active_tool.geometry, storage)

        # Remove any utility shapes
        self.delete_utility_geometry()
        self.tool_shape.clear(update=True)

        # Replot and reset tool.
        self.replot()
        # self.active_tool = type(self.active_tool)(self)

    def add_gerber_shape(self, shape, storage):
        """
        Adds a shape to the shape storage.

        :param shape: Shape to be added.
        :type shape: DrawToolShape
        :return: None
        """
        # List of DrawToolShape?
        if isinstance(shape, list):
            for subshape in shape:
                self.add_gerber_shape(subshape, storage)
            return

        assert isinstance(shape, DrawToolShape), \
            "Expected a DrawToolShape, got %s" % str(type(shape))

        assert shape.geo is not None, \
            "Shape object has empty geometry (None)"

        assert (isinstance(shape.geo, list) and len(shape.geo) > 0) or \
               not isinstance(shape.geo, list), \
            "Shape objects has empty geometry ([])"

        if isinstance(shape, DrawToolUtilityShape):
            self.utility.append(shape)
        else:
            storage.append(shape)  # TODO: Check performance

    def add_shape(self, shape):
        """
        Adds a shape to the shape storage.

        :param shape: Shape to be added.
        :type shape: DrawToolShape
        :return: None
        """

        # List of DrawToolShape?
        if isinstance(shape, list):
            for subshape in shape:
                self.add_shape(subshape)
            return

        assert isinstance(shape, DrawToolShape), \
            "Expected a DrawToolShape, got %s" % type(shape)

        assert shape.geo is not None, \
            "Shape object has empty geometry (None)"

        assert (isinstance(shape.geo, list) and len(shape.geo) > 0) or \
               not isinstance(shape.geo, list), \
            "Shape objects has empty geometry ([])"

        if isinstance(shape, DrawToolUtilityShape):
            self.utility.append(shape)
        else:
            self.storage.insert(shape)  # TODO: Check performance

    def on_canvas_click_release(self, event):
        pos_canvas = self.canvas.vispy_canvas.translate_coords(event.pos)

        self.modifiers = QtWidgets.QApplication.keyboardModifiers()

        if self.app.grid_status():
            pos = self.app.geo_editor.snap(pos_canvas[0], pos_canvas[1])
        else:
            pos = (pos_canvas[0], pos_canvas[1])

        # if the released mouse button was RMB then test if it was a panning motion or not, if not it was a context
        # canvas menu
        try:
            if event.button == 2:  # right click
                if self.app.panning_action is True:
                    self.app.panning_action = False
                else:
                    self.app.cursor = QtGui.QCursor()
                    self.app.ui.popMenu.popup(self.app.cursor.pos())
        except Exception as e:
            log.warning("Error: %s" % str(e))
            raise

        # if the released mouse button was LMB then test if we had a right-to-left selection or a left-to-right
        # selection and then select a type of selection ("enclosing" or "touching")
        try:
            if event.button == 1:  # left click
                if self.app.selection_type is not None:
                    self.draw_selection_area_handler(self.pos, pos, self.app.selection_type)
                    self.app.selection_type = None
                elif isinstance(self.active_tool, FCApertureSelect):
                    # Dispatch event to active_tool
                    # msg = self.active_tool.click(self.app.geo_editor.snap(event.xdata, event.ydata))
                    # msg = self.active_tool.click_release((self.pos[0], self.pos[1]))
                    # self.app.inform.emit(msg)
                    self.active_tool.click_release((self.pos[0], self.pos[1]))
                    self.replot()
        except Exception as e:
            log.warning("Error: %s" % str(e))
            raise

    def draw_selection_area_handler(self, start_pos, end_pos, sel_type):
        """
        :param start_pos: mouse position when the selection LMB click was done
        :param end_pos: mouse position when the left mouse button is released
        :param sel_type: if True it's a left to right selection (enclosure), if False it's a 'touch' selection
        :type Bool
        :return:
        """
        poly_selection = Polygon([start_pos, (end_pos[0], start_pos[1]), end_pos, (start_pos[0], end_pos[1])])

        self.app.delete_selection_shape()
        for storage in self.storage_dict:
            for obj in self.storage_dict[storage].get_objects():
                if (sel_type is True and poly_selection.contains(obj.geo)) or \
                        (sel_type is False and poly_selection.intersects(obj.geo)):
                    if self.key == self.app.defaults["global_mselect_key"]:
                        if obj in self.selected:
                            self.selected.remove(obj)
                        else:
                            # add the object to the selected shapes
                            self.selected.append(obj)
                    else:
                        self.selected.append(obj)

        # select the diameter of the selected shape in the tool table
        for storage in self.storage_dict:
            for shape_s in self.selected:
                if shape_s in self.storage_dict[storage].get_objects():
                    for key in self.tool2tooldia:
                        if self.tool2tooldia[key] == storage:
                            item = self.apertures_table.item((key - 1), 1)
                            self.apertures_table.setCurrentItem(item)
                            self.last_aperture_selected = key
                            # item.setSelected(True)
                            # self.grb_editor_app.apertures_table.selectItem(key - 1)

        self.replot()

    def on_canvas_move(self, event):
        """
        Called on 'mouse_move' event

        event.pos have canvas screen coordinates

        :param event: Event object dispatched by VisPy SceneCavas
        :return: None
        """

        pos = self.canvas.vispy_canvas.translate_coords(event.pos)
        event.xdata, event.ydata = pos[0], pos[1]

        self.x = event.xdata
        self.y = event.ydata

        # Prevent updates on pan
        # if len(event.buttons) > 0:
        #     return

        # if the RMB is clicked and mouse is moving over plot then 'panning_action' is True
        if event.button == 2:
            self.app.panning_action = True
            return
        else:
            self.app.panning_action = False

        try:
            x = float(event.xdata)
            y = float(event.ydata)
        except TypeError:
            return

        if self.active_tool is None:
            return

        ### Snap coordinates
        x, y = self.app.geo_editor.app.geo_editor.snap(x, y)

        self.snap_x = x
        self.snap_y = y

        # update the position label in the infobar since the APP mouse event handlers are disconnected
        self.app.ui.position_label.setText("&nbsp;&nbsp;&nbsp;&nbsp;<b>X</b>: %.4f&nbsp;&nbsp;   "
                                       "<b>Y</b>: %.4f" % (x, y))

        if self.pos is None:
            self.pos = (0, 0)
        dx = x - self.pos[0]
        dy = y - self.pos[1]

        # update the reference position label in the infobar since the APP mouse event handlers are disconnected
        self.app.ui.rel_position_label.setText("<b>Dx</b>: %.4f&nbsp;&nbsp;  <b>Dy</b>: "
                                           "%.4f&nbsp;&nbsp;&nbsp;&nbsp;" % (dx, dy))

        ### Utility geometry (animated)
        geo = self.active_tool.utility_geometry(data=(x, y))

        if isinstance(geo, DrawToolShape) and geo.geo is not None:

            # Remove any previous utility shape
            self.tool_shape.clear(update=True)
            self.draw_utility_geometry(geo=geo)

        ### Selection area on canvas section ###
        dx = pos[0] - self.pos[0]
        if event.is_dragging == 1 and event.button == 1:
            self.app.delete_selection_shape()
            if dx < 0:
                self.app.draw_moving_selection_shape((self.pos[0], self.pos[1]), (x,y),
                     color=self.app.defaults["global_alt_sel_line"],
                     face_color=self.app.defaults['global_alt_sel_fill'])
                self.app.selection_type = False
            else:
                self.app.draw_moving_selection_shape((self.pos[0], self.pos[1]), (x,y))
                self.app.selection_type = True
        else:
            self.app.selection_type = None

        # Update cursor
        self.app.app_cursor.set_data(np.asarray([(x, y)]), symbol='++', edge_color='black', size=20)

    def on_canvas_key_release(self, event):
        self.key = None

    def draw_utility_geometry(self, geo):
            # Add the new utility shape
            try:
                # this case is for the Font Parse
                for el in list(geo.geo):
                    if type(el) == MultiPolygon:
                        for poly in el:
                            self.tool_shape.add(
                                shape=poly,
                                color=(self.app.defaults["global_draw_color"] + '80'),
                                update=False,
                                layer=0,
                                tolerance=None
                            )
                    elif type(el) == MultiLineString:
                        for linestring in el:
                            self.tool_shape.add(
                                shape=linestring,
                                color=(self.app.defaults["global_draw_color"] + '80'),
                                update=False,
                                layer=0,
                                tolerance=None
                            )
                    else:
                        self.tool_shape.add(
                            shape=el,
                            color=(self.app.defaults["global_draw_color"] + '80'),
                            update=False,
                            layer=0,
                            tolerance=None
                        )
            except TypeError:
                self.tool_shape.add(
                    shape=geo.geo, color=(self.app.defaults["global_draw_color"] + '80'),
                    update=False, layer=0, tolerance=None)

            self.tool_shape.redraw()

    def replot(self):
        self.plot_all()

    def plot_all(self):
        """
        Plots all shapes in the editor.

        :return: None
        :rtype: None
        """
        with self.app.proc_container.new("Plotting"):
            # self.app.log.debug("plot_all()")
            self.shapes.clear(update=True)

            for storage in self.storage_dict:
                for shape in self.storage_dict[storage]['solid_geometry']:
                    if shape.geo is None:
                        continue

                    if shape in self.selected:
                        self.plot_shape(geometry=shape.geo, color=self.app.defaults['global_sel_draw_color'],
                                        linewidth=2)
                        continue
                    self.plot_shape(geometry=shape.geo, color=self.app.defaults['global_draw_color'])

            for shape in self.utility:
                self.plot_shape(geometry=shape.geo, linewidth=1)
                continue

            self.shapes.redraw()

    def start_delayed_plot(self, check_period):
        # self.plot_thread = threading.Thread(target=lambda: self.check_plot_finished(check_period))
        # self.plot_thread.start()
        self.plot_thread = QtCore.QTimer()
        self.plot_thread.setInterval(check_period)
        self.plot_thread.timeout.connect(self.check_plot_finished)
        self.plot_thread.start()

    def check_plot_finished(self):
        try:
            if self.app.collection.has_plot_promises() is False:
                self.plot_thread.stop()
                self.plot_all()
                log.debug("FlatCAMGrbEditor --> delayed_plot finished")
        except Exception:
            traceback.print_exc()

    # def stop_delayed_plot(self):
    #     self.plot_thread.exit()
    #     # self.plot_thread.join()

    # def check_plot_finished(self, delay):
    #     """
    #     Using Alfe's answer from here:
    #     https://stackoverflow.com/questions/474528/what-is-the-best-way-to-repeatedly-execute-a-function-every-x-seconds-in-python
    #
    #     :param delay: period of checking if project file size is more than zero; in seconds
    #     :param filename: the name of the project file to be checked for size more than zero
    #     :return:
    #     """
    #     next_time = time.time() + delay
    #     while True:
    #         time.sleep(max(0, next_time - time.time()))
    #         try:
    #             if self.app.collection.has_plot_promises() is False:
    #                 self.plot_all()
    #                 break
    #         except Exception:
    #             traceback.print_exc()
    #
    #         # skip tasks if we are behind schedule:
    #         next_time += (time.time() - next_time) // delay * delay + delay

    def plot_shape(self, geometry=None, color='black', linewidth=1):
        """
        Plots a geometric object or list of objects without rendering. Plotted objects
        are returned as a list. This allows for efficient/animated rendering.

        :param geometry: Geometry to be plotted (Any Shapely.geom kind or list of such)
        :param color: Shape color
        :param linewidth: Width of lines in # of pixels.
        :return: List of plotted elements.
        """
        # plot_elements = []

        if geometry is None:
            geometry = self.active_tool.geometry

        try:
            self.shapes.add(shape=geometry.geo, color=color, face_color=color, layer=0)
        except AttributeError:
            if type(geometry) == Point:
                return
            self.shapes.add(shape=geometry, color=color, face_color=color+'AF', layer=0)

    def on_shape_complete(self):
        self.app.log.debug("on_shape_complete()")

        # Add shape
        self.add_shape(self.active_tool.geometry)

        # Remove any utility shapes
        self.delete_utility_geometry()
        self.tool_shape.clear(update=True)

        # Replot and reset tool.
        self.replot()
        # self.active_tool = type(self.active_tool)(self)

    def get_selected(self):
        """
        Returns list of shapes that are selected in the editor.

        :return: List of shapes.
        """
        # return [shape for shape in self.shape_buffer if shape["selected"]]
        return self.selected

    def delete_selected(self):
        temp_ref = [s for s in self.selected]
        for shape_sel in temp_ref:
            self.delete_shape(shape_sel)

        self.selected = []
        self.build_ui()
        self.app.inform.emit(_("[success] Done. Drill(s) deleted."))

    def delete_shape(self, shape):
        self.is_modified = True

        if shape in self.utility:
            self.utility.remove(shape)
            return

        for storage in self.storage_dict:
            # try:
            #     self.storage_dict[storage].remove(shape)
            # except:
            #     pass
            if shape in self.storage_dict[storage].get_objects():
                self.storage_dict[storage].remove(shape)
                # a hack to make the tool_table display less drills per diameter
                # self.points_edit it's only useful first time when we load the data into the storage
                # but is still used as referecen when building tool_table in self.build_ui()
                # the number of drills displayed in column 2 is just a len(self.points_edit) therefore
                # deleting self.points_edit elements (doesn't matter who but just the number) solved the display issue.
                del self.points_edit[storage][0]

        if shape in self.selected:
            self.selected.remove(shape)  # TODO: Check performance

    def delete_utility_geometry(self):
        # for_deletion = [shape for shape in self.shape_buffer if shape.utility]
        # for_deletion = [shape for shape in self.storage.get_objects() if shape.utility]
        for_deletion = [shape for shape in self.utility]
        for shape in for_deletion:
            self.delete_shape(shape)

        self.tool_shape.clear(update=True)
        self.tool_shape.redraw()

    def on_delete_btn(self):
        self.delete_selected()
        self.replot()

    def select_tool(self, toolname):
        """
        Selects a drawing tool. Impacts the object and GUI.

        :param toolname: Name of the tool.
        :return: None
        """
        self.tools_gerber[toolname]["button"].setChecked(True)
        self.on_tool_select(toolname)

    def set_selected(self, shape):

        # Remove and add to the end.
        if shape in self.selected:
            self.selected.remove(shape)

        self.selected.append(shape)

    def set_unselected(self, shape):
        if shape in self.selected:
            self.selected.remove(shape)

    def on_array_type_combo(self):
        if self.array_type_combo.currentIndex() == 0:
            self.array_circular_frame.hide()
            self.array_linear_frame.show()
        else:
            self.delete_utility_geometry()
            self.array_circular_frame.show()
            self.array_linear_frame.hide()
            self.app.inform.emit(_("Click on the circular array Center position"))

    def on_linear_angle_radio(self):
        val = self.drill_axis_radio.get_value()
        if val == 'A':
            self.linear_angle_spinner.show()
            self.linear_angle_label.show()
        else:
            self.linear_angle_spinner.hide()
            self.linear_angle_label.hide()

    def exc_add_drill(self):
        self.select_tool('add')
        return

    def exc_add_drill_array(self):
        self.select_tool('add_array')
        return

    def exc_resize_drills(self):
        self.select_tool('resize')
        return

    def exc_copy_drills(self):
        self.select_tool('copy')
        return

    def exc_move_drills(self):
        self.select_tool('move')
        return