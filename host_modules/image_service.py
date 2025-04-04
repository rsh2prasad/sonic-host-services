"""
This module provides services related to SONiC images, including:
1) Downloading images
2) Installing images
3) Calculating checksums for images
"""

import errno
import hashlib
import logging
import os
import requests
import stat
import subprocess
import json

from host_modules import host_service
import tempfile

MOD_NAME = "image_service"

DEFAULT_IMAGE_SAVE_AS = "/tmp/downloaded-sonic.bin"

logger = logging.getLogger(__name__)


class ImageService(host_service.HostModule):
    """DBus endpoint that handles downloading and installing SONiC images"""

    @host_service.method(
        host_service.bus_name(MOD_NAME), in_signature="ss", out_signature="is"
    )
    def download(self, image_url, save_as):
        """
        Download a SONiC image.

        Args:
             image_url: url for remote image.
             save_as: local path for the downloaded image. The directory must exist and be *all* writable.
        """
        logger.info("Download new sonic image from {} as {}".format(image_url, save_as))
        # Check if the directory exists, is absolute and has write permission.
        if not os.path.isabs(save_as):
            logger.error("The path {} is not an absolute path".format(save_as))
            return errno.EINVAL, "Path is not absolute"
        dir = os.path.dirname(save_as)
        if not os.path.isdir(dir):
            logger.error("Directory {} does not exist".format(dir))
            return errno.ENOENT, "Directory does not exist"
        st_mode = os.stat(dir).st_mode
        if (
            not (st_mode & stat.S_IWUSR)
            or not (st_mode & stat.S_IWGRP)
            or not (st_mode & stat.S_IWOTH)
        ):
            logger.error("Directory {} is not all writable {}".format(dir, st_mode))
            return errno.EACCES, "Directory is not all writable"
        try:
            response = requests.get(image_url, stream=True)
            if response.status_code != 200:
                logger.error(
                    "Failed to download image: HTTP status code {}".format(
                        response.status_code
                    )
                )
                return errno.EIO, "HTTP error: {}".format(response.status_code)

            with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                temp_file_path = tmp_file.name
            os.replace(temp_file_path, save_as)
            return 0, "Download successful"
        except Exception as e:
            logger.error("Failed to write downloaded image to disk: {}".format(e))
            return errno.EIO, str(e)

    @host_service.method(
        host_service.bus_name(MOD_NAME), in_signature="s", out_signature="is"
    )
    def install(self, where):
        """
        Install a a sonic image:

        Args:
            where: either a local path or a remote url pointing to the image.
        """
        logger.info("Using sonic-installer to install the image at {}.".format(where))
        cmd = ["/usr/local/bin/sonic-installer", "install", "-y", where]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        msg = ""
        if result.returncode:
            lines = result.stderr.decode().split("\n")
            for line in lines:
                if "Error" in line:
                    msg = line
                    break
        return result.returncode, msg

    @host_service.method(
        host_service.bus_name(MOD_NAME), in_signature="ss", out_signature="is"
    )
    def checksum(self, file_path, algorithm):
        """
        Calculate the checksum of a file.

        Args:
            file_path: path to the file.
            algorithm: checksum algorithm to use (sha256, sha512, md5).
        """

        logger.info("Calculating {} checksum for file {}".format(algorithm, file_path))

        if not os.path.isfile(file_path):
            logger.error("File {} does not exist".format(file_path))
            return errno.ENOENT, "File does not exist"

        hash_func = None
        if algorithm == "sha256":
            hash_func = hashlib.sha256()
        elif algorithm == "sha512":
            hash_func = hashlib.sha512()
        elif algorithm == "md5":
            hash_func = hashlib.md5()
        else:
            logger.error("Unsupported algorithm: {}".format(algorithm))
            return errno.EINVAL, "Unsupported algorithm"

        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_func.update(chunk)
            return 0, hash_func.hexdigest()
        except Exception as e:
            logger.error("Failed to calculate checksum: {}".format(e))
            return errno.EIO, str(e)

    @host_service.method(
        host_service.bus_name(MOD_NAME), in_signature="", out_signature="is"
    )
    def list_images(self):
        """
        List the current, next, and available SONiC images.

        Returns:
            A tuple with an error code and a JSON string with keys "current", "next", and "available" or an error message.
        """
        logger.info("Listing SONiC images")

        try:
            output = subprocess.check_output(
                ["/usr/local/bin/sonic-installer", "list"],
                stderr=subprocess.STDOUT,
            ).decode().strip()
            result = self._parse_sonic_installer_list(output)
            logger.info("List result: {}".format(result))
            return 0, json.dumps(result)
        except subprocess.CalledProcessError as e:
            msg = "Failed to list images: command {} failed with return code {} and message {}".format(e.cmd, e.returncode, e.output.decode())
            logger.error(msg)
            return e.returncode, msg

    @host_service.method(
        host_service.bus_name(MOD_NAME), in_signature="s", out_signature="is"
    )
    def set_next_boot(self, image):
        """
        Set the image to be used for the next boot.

        Args:
            image: The name of the image to set for the next boot.
        """
        logger.info("Setting the next boot image to {}".format(image))
        cmd = ["/usr/local/bin/sonic-installer", "set-next-boot", image]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        msg = "Boot image set to {}".format(image)
        logger.info(msg)
        if result.returncode:
            logger.error("Failed to set next boot image: {}".format(result.stderr.decode()))
            msg = result.stderr.decode()
            # sonic-installer might not return a proper error code, so we need to check the message.
            if "not" in msg.lower() and ("exist" in msg.lower() or "found" in msg.lower()):
                return errno.ENOENT, msg
        return result.returncode, msg


    def _parse_sonic_installer_list(self, output):
        """
        Parse the output of the sonic-installer list command.

        Args:
            output: The output of the sonic-installer list command.

        Returns:
            A dictionary with keys "current", "next", and "available" containing the respective images.
        """
        current_image = ""
        next_image = ""
        available_images = []

        for line in output.split("\n"):
            if "current:" in line.lower():
                parts = line.split(":")
                if len(parts) > 1:
                    current_image = parts[1].strip()
            elif "next:" in line.lower():
                parts = line.split(":")
                if len(parts) > 1:
                    next_image = parts[1].strip()
            elif "available:" in line.lower():
                continue
            else:
                available_images.append(line.strip())

        logger.info("Current image: {}".format(current_image))
        logger.info("Next image: {}".format(next_image))
        logger.info("Available images: {}".format(available_images))
        return {
            "current": current_image or "",
            "next": next_image or "",
            "available": available_images or [],
        }