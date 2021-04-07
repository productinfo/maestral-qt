# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 16:23:13 2018

@author: samschott
"""

# system imports
import os
import os.path as osp
from PyQt5 import QtGui, QtCore, QtWidgets, uic
from PyQt5.QtCore import QModelIndex, Qt

# maestral modules
from maestral.utils.appdirs import get_home_dir
from maestral.utils.path import delete

# local imports
from .resources import APP_ICON_PATH, SETUP_DIALOG_PATH, native_folder_icon
from .utils import IS_MACOS, MaestralBackgroundTask, icon_to_pixmap, is_empty
from .widgets import UserDialog
from .selective_sync_dialog import AsyncListFolder, DropboxTreeModel, DropboxPathItem


# noinspection PyArgumentList
class SetupDialog(QtWidgets.QDialog):
    """A dialog to link and set up a new Dropbox account."""

    accepted = False

    def __init__(self, mdbx, parent=None):
        super().__init__(parent=parent)
        # load user interface layout from .ui file
        uic.loadUi(SETUP_DIALOG_PATH, self)

        if IS_MACOS:
            self.setWindowFlags(Qt.WindowStaysOnTopHint)

        self.mdbx = mdbx
        self.config_name = self.mdbx.config_name
        self.dbx_model = None
        self.excluded_items = []

        self.app_icon = QtGui.QIcon(APP_ICON_PATH)

        self.labelIcon_0.setPixmap(icon_to_pixmap(self.app_icon, 150))
        self.labelIcon_1.setPixmap(icon_to_pixmap(self.app_icon, 70))
        self.labelIcon_2.setPixmap(icon_to_pixmap(self.app_icon, 70))
        self.labelIcon_3.setPixmap(icon_to_pixmap(self.app_icon, 120))

        # prepare auth session
        self.auth_url = self.mdbx.get_auth_url()
        prompt = self.labelAuthLink.text().format(self.auth_url)
        self.labelAuthLink.setText(prompt)

        # set up Dropbox location combobox

        dropbox_dir = self.mdbx.get_conf("main", "path")

        if dropbox_dir == "":
            dropbox_dir = f"{get_home_dir()}/Dropbox ({self.config_name.capitalize()})"

        self.comboBoxDropboxPath.addItem(native_folder_icon(), dropbox_dir)
        self.comboBoxDropboxPath.insertSeparator(1)
        self.comboBoxDropboxPath.addItem(QtGui.QIcon(), "Choose...")
        self.comboBoxDropboxPath.currentIndexChanged.connect(self.on_combobox)

        # resize dialog buttons
        width = self.pushButtonAuthPageCancel.width() * 1.1
        for b in (
            self.pushButtonAuthPageLink,
            self.pushButtonDropboxPathUnlink,
            self.pushButtonDropboxPathSelect,
            self.pushButtonFolderSelectionBack,
            self.pushButtonFolderSelectionSelect,
            self.pushButtonAuthPageCancel,
            self.pushButtonDropboxPathCalcel,
            self.pushButtonClose,
        ):
            b.setMinimumWidth(width)
            b.setMaximumWidth(width)

        self.dropbox_folder_dialog = QtWidgets.QFileDialog(self)
        self.dropbox_folder_dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptOpen)
        self.dropbox_folder_dialog.setFileMode(QtWidgets.QFileDialog.Directory)
        self.dropbox_folder_dialog.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        self.dropbox_folder_dialog.setLabelText(QtWidgets.QFileDialog.Accept, "Select")
        self.dropbox_folder_dialog.setDirectory(get_home_dir())
        self.dropbox_folder_dialog.fileSelected.connect(self.on_new_dbx_folder)
        self.dropbox_folder_dialog.rejected.connect(
            lambda: self.comboBoxDropboxPath.setCurrentIndex(0)
        )

        # connect buttons to callbacks
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.pushButtonLink.clicked.connect(self.on_link_clicked)
        self.pushButtonAuthPageCancel.clicked.connect(self.on_reject_requested)
        self.pushButtonAuthPageLink.clicked.connect(self.on_auth_clicked)
        self.pushButtonDropboxPathCalcel.clicked.connect(self.on_reject_requested)
        self.pushButtonDropboxPathSelect.clicked.connect(
            self.on_dropbox_location_selected
        )
        self.pushButtonDropboxPathUnlink.clicked.connect(self.unlink_and_go_to_start)
        self.pushButtonFolderSelectionBack.clicked.connect(
            self.stackedWidget.slideInPrev
        )
        self.pushButtonFolderSelectionSelect.clicked.connect(self.on_folders_selected)
        self.pushButtonClose.clicked.connect(self.on_accept_requested)
        self.selectAllCheckBox.clicked.connect(self.on_select_all_clicked)

        # check if we are already authenticated, skip authentication if yes
        if not self.mdbx.pending_link:
            self.labelDropboxPath.setText(
                """
                <html><head/><body>
                <p align="left">
                Your Dropbox folder has been moved or deleted from its original
                location. Syncing will not work until you move it back.
                <p align="left">
                To move it back, click "Quit" below, move the Dropbox folder back to its
                original location, and launch Maestral again.
                </p>
                <p align="left">
                To re-download your Dropbox, please select a new folder below. Select
                "Unlink" to unlink your Dropbox account from Maestral.
                </p>
                </body></html>
                """
            )
            self.pushButtonDropboxPathCalcel.setText("Quit")
            self.stackedWidget.setCurrentIndex(2)
            self.stackedWidgetButtons.setCurrentIndex(2)

        else:
            self.stackedWidget.setCurrentIndex(0)
            self.stackedWidgetButtons.setCurrentIndex(0)

    # =============================================================================
    # Main callbacks
    # =============================================================================

    def closeEvent(self, event):

        if self.stackedWidget.currentIndex == 4:
            self.on_accept_requested()
        else:
            self.on_reject_requested()

    @QtCore.pyqtSlot()
    def on_accept_requested(self):
        del self.mdbx

        self.accepted = True
        self.accept()

    @QtCore.pyqtSlot()
    def on_reject_requested(self):
        self.accepted = False
        self.reject()

    def unlink_and_go_to_start(self):
        self.mdbx.unlink()
        self.stackedWidget.slideInIdx(0)

    @QtCore.pyqtSlot()
    def on_link_clicked(self):

        self.stackedWidget.fadeInIdx(1)
        self.pushButtonAuthPageLink.setFocus()

    @QtCore.pyqtSlot()
    def on_auth_clicked(self):

        if self.lineEditAuthCode.text() == "":
            msg = "Please enter an authentication token."
            msg_box = UserDialog("Authentication failed.", msg, parent=self)
            msg_box.open()
        else:
            self.progressIndicator.startAnimation()
            self.pushButtonAuthPageLink.setEnabled(False)
            self.lineEditAuthCode.setEnabled(False)

            self.link_async()

    def link_async(self):

        token = self.lineEditAuthCode.text()

        self.auth_task = MaestralBackgroundTask(
            parent=self, config_name=self.mdbx.config_name, target="link", args=(token,)
        )
        self.auth_task.sig_result.connect(self.on_link_done)

    def on_link_done(self, res):

        if res == 0:
            # switch to next page
            self.stackedWidget.slideInIdx(2)
            self.pushButtonDropboxPathSelect.setFocus()
            self.lineEditAuthCode.clear()  # clear since we might come back on unlink

        elif res == 1:
            msg = "Please make sure that you entered the correct authentication token."
            msg_box = UserDialog("Authentication failed.", msg, parent=self)
            msg_box.open()
        elif res == 2:
            msg = (
                "Please make sure that you are connected to the internet and try again."
            )
            msg_box = UserDialog("Connection failed.", msg, parent=self)
            msg_box.open()

        self.progressIndicator.stopAnimation()
        self.pushButtonAuthPageLink.setEnabled(True)
        self.lineEditAuthCode.setEnabled(True)

    @QtCore.pyqtSlot()
    def on_dropbox_location_selected(self):

        # start with clean sync state
        self.mdbx.reset_sync_state()

        # apply dropbox path

        try:

            if osp.exists(self.dropbox_location):

                if is_empty(self.dropbox_location):
                    delete(self.dropbox_location, raise_error=True)
                else:
                    msg_box = UserDialog(
                        title="Folder is not empty",
                        message=(
                            f'The folder "{osp.basename(self.dropbox_location)}" is '
                            "not empty. Would you like to delete its content or merge "
                            "it with your Dropbox?"
                        ),
                        button_names=("Delete", "Cancel", "Merge"),
                        parent=self,
                    )
                    msg_box.setAcceptButtonIcon("edit-clear")
                    res = msg_box.exec_()

                    if res == UserDialog.Rejected:
                        return
                    elif res == UserDialog.Accepted:
                        delete(self.dropbox_location, raise_error=True)
                    elif res == UserDialog.Accepted2:
                        pass

            self.mdbx.create_dropbox_directory(self.dropbox_location)
        except OSError:
            msg_box = UserDialog(
                title="Could not set directory",
                message=(
                    "Please check if you have permissions to write to the "
                    "selected location."
                ),
                parent=self,
            )
            msg_box.exec_()
            return

        # switch to next page
        self.mdbx.set_conf("main", "excluded_items", [])
        self.stackedWidget.slideInIdx(3)
        self.treeViewFolders.setFocus()

        # populate folder list
        if not self.excluded_items:  # don't repopulate
            self.populate_folders_list()

    @QtCore.pyqtSlot()
    def on_folders_selected(self):

        self.update_selection()
        self.mdbx.excluded_items = self.excluded_items

        # if any excluded items are currently on the drive, delete them
        for item in self.excluded_items:
            local_item = self.mdbx.to_local_path(item)
            delete(local_item)

        # switch to next page
        self.stackedWidget.slideInIdx(4)

    # =============================================================================
    # Helper functions
    # =============================================================================

    @QtCore.pyqtSlot(int)
    def on_combobox(self, idx):
        if idx == 2:
            self.dropbox_folder_dialog.open()

    @QtCore.pyqtSlot(str)
    def on_new_dbx_folder(self, new_location):
        self.comboBoxDropboxPath.setCurrentIndex(0)
        if not new_location == "":
            self.comboBoxDropboxPath.setItemText(0, new_location)

        self.dropbox_location = new_location

    def populate_folders_list(self):
        self.pushButtonFolderSelectionSelect.setEnabled(False)

        self.async_loader = AsyncListFolder(self.mdbx.config_name, self)
        self.dbx_root = DropboxPathItem(self.mdbx, self.async_loader)
        self.dbx_model = DropboxTreeModel(self.dbx_root)
        self.dbx_model.dataChanged.connect(self.update_select_all_checkbox)
        self.treeViewFolders.setModel(self.dbx_model)

        self.dbx_model.loading_done.connect(
            lambda: self.pushButtonFolderSelectionSelect.setEnabled(True)
        )
        self.dbx_model.loading_failed.connect(
            lambda: self.pushButtonFolderSelectionSelect.setEnabled(False)
        )

        self.dbx_model.loading_done.connect(
            lambda: self.selectAllCheckBox.setEnabled(True)
        )
        self.dbx_model.loading_failed.connect(
            lambda: self.selectAllCheckBox.setEnabled(False)
        )

        self.dbx_model.loading_done.connect(
            lambda: self.treeViewFolders.resizeColumnToContents(0)
        )

    @QtCore.pyqtSlot()
    def update_select_all_checkbox(self):
        check_states = []
        for irow in range(self.dbx_model._root_item.child_count_loaded()):
            index = self.dbx_model.index(irow, 0, QModelIndex())
            check_states.append(self.dbx_model.data(index, Qt.CheckStateRole))
        if all(cs == 2 for cs in check_states):
            self.selectAllCheckBox.setChecked(True)
        else:
            self.selectAllCheckBox.setChecked(False)

    @QtCore.pyqtSlot(bool)
    def on_select_all_clicked(self, checked):
        checked_state = 2 if checked else 0
        for irow in range(self.dbx_model._root_item.child_count_loaded()):
            index = self.dbx_model.index(irow, 0, QModelIndex())
            self.dbx_model.setCheckState(index, checked_state)

    def update_selection(self, index=QModelIndex()):

        if index.isValid():
            item = index.internalPointer()
            item_dbx_path = item._path.lower()

            # We have started with all folders included. Therefore just append excluded
            # folders here.
            if item.checkState == 0:
                self.excluded_items.append(item_dbx_path)
        else:
            item = self.dbx_model._root_item

        for row in range(item.child_count_loaded()):
            index_child = self.dbx_model.index(row, 0, index)
            self.update_selection(index=index_child)

    def changeEvent(self, QEvent):

        if QEvent.type() == QtCore.QEvent.PaletteChange:
            self.update_dark_mode()

    def update_dark_mode(self):
        if self.dbx_model:
            self.dbx_model.reloadData([Qt.DecorationRole])  # reload folder icons

    # static method to create the dialog and return Maestral instance on success
    @staticmethod
    def configureMaestral(mdbx, parent=None):
        fsd = SetupDialog(mdbx, parent)
        fsd.show()
        fsd.exec_()

        return fsd.accepted
