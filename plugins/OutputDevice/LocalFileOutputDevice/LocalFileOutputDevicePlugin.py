# Copyright (c) 2015 Ultimaker B.V.
# Uranium is released under the terms of the AGPLv3 or higher.

import os.path
import sys

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QFileDialog, QMessageBox

from UM.Application import Application
from UM.Preferences import Preferences
from UM.Logger import Logger
from UM.Mesh.MeshWriter import MeshWriter
from UM.Mesh.WriteMeshJob import WriteMeshJob
from UM.OutputDevice.OutputDevicePlugin import OutputDevicePlugin
from UM.OutputDevice.OutputDevice import OutputDevice
from UM.OutputDevice import OutputDeviceError
from UM.Message import Message

from UM.i18n import i18nCatalog
catalog = i18nCatalog("uranium")

##  Implements an OutputDevicePlugin that provides a single instance of LocalFileOutputDevice
class LocalFileOutputDevicePlugin(OutputDevicePlugin):
    def __init__(self):
        super().__init__()

        Preferences.getInstance().addPreference("local_file/last_used_type", "")
        Preferences.getInstance().addPreference("local_file/dialog_state", "")

    def start(self):
        self.getOutputDeviceManager().addOutputDevice(LocalFileOutputDevice())

    def stop(self):
        self.getOutputDeviceManager().removeOutputDevice("local_file")

##  Implements an OutputDevice that supports saving to arbitrary local files.
class LocalFileOutputDevice(OutputDevice):
    def __init__(self):
        super().__init__("local_file")

        self.setName(catalog.i18nc("Local File Output Device Name", "Local File"))
        self.setShortDescription(catalog.i18n("Save to File"))
        self.setDescription(catalog.i18n("Save to File"))
        self.setIconName("save")

    def requestWrite(self, node):
        dialog = QFileDialog()
        dialog.setWindowTitle(catalog.i18n("Save to File"))
        dialog.setFileMode(QFileDialog.AnyFile)
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        # Ensure platform never ask for overwrite confirmation since we do this ourselves
        dialog.setOption(QFileDialog.DontConfirmOverwrite)

        if sys.platform == "linux":
            dialog.setOption(QFileDialog.DontUseNativeDialog)

        filters = []
        mime_types = []
        selected_filter = None
        last_used_type = Preferences.getInstance().getValue("local_file/last_used_type")

        file_types = Application.getInstance().getMeshFileHandler().getSupportedFileTypesWrite()
        file_types.sort(key = lambda k: k["description"])

        for item in file_types:
            type_filter = "{0} (*.{1})".format(item["description"], item["extension"])
            filters.append(type_filter)
            mime_types.append(item["mime_type"])
            if last_used_type == item["mime_type"]:
                selected_filter = type_filter

        dialog.setNameFilters(filters)
        if selected_filter != None:
            dialog.selectNameFilter(selected_filter)

        dialog.restoreState(Preferences.getInstance().getValue("local_file/dialog_state").encode())

        if not dialog.exec_():
            raise OutputDeviceError.UserCancelledError()

        Preferences.getInstance().setValue("local_file/dialog_state", str(dialog.saveState()))

        selected_type = file_types[filters.index(dialog.selectedNameFilter())]
        Preferences.getInstance().setValue("local_file/last_used_type", selected_type["mime_type"])

        file_name = dialog.selectedFiles()[0]

        if os.path.exists(file_name):
            result = QMessageBox.question(None, catalog.i18n("File Already Exists"), catalog.i18n("The file {0} already exists. Are you sure you want to overwrite it?").format(file_name))
            if result == QMessageBox.No:
                raise OutputDeviceError.UserCancelledError()

        self.writeStarted.emit(self)
        mesh_writer = Application.getInstance().getMeshFileHandler().getWriter(selected_type["id"])
        try:
            mode = selected_type["mode"]
            if mode == MeshWriter.OutputMode.TextMode:
                Logger.log("d", "Writing to Local File %s in text mode", file_name)
                stream = open(file_name, "wt")
            elif mode == MeshWriter.OutputMode.BinaryMode:
                Logger.log("d", "Writing to Local File %s in binary mode", file_name)
                stream = open(file_name, "wb")
            else:
                Logger.log("e", "Unknown file output mode %s", selected_type["mode"])
                raise OutputDeviceError.InvalidModeError()

            job = WriteMeshJob(mesh_writer, stream, node, mode)
            job.setFileName(file_name)
            job.progress.connect(self._onJobProgress)
            job.finished.connect(self._onWriteJobFinished)

            message = Message(catalog.i18nc("", "Saving to {0}").format(file_name), 0, False, -1)
            message.show()

            job._message = message
            job.start()
        except PermissionError as e:
            raise OutputDeviceError.PermissionDeniedError() from e
        except OSError as e:
            raise OutputDeviceError.WriteRequestFailedError() from e

    def _onJobProgress(self, job, progress):
        if hasattr(job, "_message"):
            job._message.setProgress(progress)
        self.writeProgress.emit(self, progress)

    def _onWriteJobFinished(self, job):
        if hasattr(job, "_message"):
            job._message.hide()
            job._message = None

        self.writeFinished.emit(self)
        if job.getResult():
            self.writeSuccess.emit(self)
            message = Message(catalog.i18nc("", "Saved to {0}").format(job.getFileName()))
            message.addAction("open_folder", catalog.i18nc("", "Open Folder"), "open-folder", catalog.i18nc("","Open the folder containing the file"))
            message._folder = os.path.dirname(job.getFileName())
            message.actionTriggered.connect(self._onMessageActionTriggered)
            message.show()
        else:
            message = Message(catalog.i18nc("", "Could not save to {0}: {1}").format(job.getFileName(), str(job.getError())))
            message.show()
            self.writeError.emit(self)
        job.getStream().close()

    def _onMessageActionTriggered(self, message, action):
        if action == "open_folder" and hasattr(message, "_folder"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(message._folder))
