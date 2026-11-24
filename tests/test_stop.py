"""A stop asked for (the app's Force stop sends "cancel") never lets a half-done change through: an update
stopped once its blobs are here leaves the folder as it was; a deploy or a publish stopped before its new
manifest goes up leaves players on the release before."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deltapatcher import client, deploy, publish, serve  # noqa: E402
from helpers import TempCase, read, snapshot, write  # noqa: E402


class StopTests(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.src, self.store = self.p("src"), self.p("store")
        write(self.p("src", "readme.txt"), "release one\n")
        write(self.p("src", "maps", "a.bin"), os.urandom(40 * 1024))
        publish.publish(self.src, self.store, "Game", "1.0")

    def release_two(self):
        write(self.p("src", "readme.txt"), "release two\n")
        write(self.p("src", "maps", "b.bin"), os.urandom(60 * 1024))
        publish.publish(self.src, self.store, "Game", "2.0")

    def test_an_update_stopped_after_its_downloads_changes_nothing(self):
        srv, _t = serve.serve_in_background(self.store, "127.0.0.1", 0)
        try:
            url = "http://127.0.0.1:%d/" % srv.server_address[1]
            inst = self.p("install")
            client.update(url, inst)
            before = snapshot(inst)
            self.release_two()
            stop = threading.Event()

            def progress(done, total, label):
                if label == "downloading" and total and done >= total:
                    stop.set()                  # every blob is here: the stop comes before the first file moves

            with self.assertRaises(Exception) as caught:
                client.update(url, inst, progress=progress, cancel=stop)
            self.assertIn("stopped", str(caught.exception))
            self.assertEqual(snapshot(inst), before)
            client.update(url, inst)            # not stopped: it goes through
            self.assertEqual(read(os.path.join(inst, "readme.txt")), b"release two\n")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_a_deploy_stopped_before_its_manifest_keeps_the_release_before(self):
        www = self.p("www")
        deploy.deploy(self.store, deploy.FolderTarget(www))
        live = read(os.path.join(www, "manifest.json"))
        self.release_two()
        stop = threading.Event()

        def progress(done, total, label):
            if label == "uploading" and total and done >= total:
                stop.set()                      # every blob is up: the stop comes before the manifest

        with self.assertRaises(deploy.DeployError) as caught:
            deploy.deploy(self.store, deploy.FolderTarget(www), progress=progress, cancel=stop)
        self.assertIn("before the new manifest went up", str(caught.exception))
        self.assertEqual(read(os.path.join(www, "manifest.json")), live)
        again = deploy.deploy(self.store, deploy.FolderTarget(www))
        self.assertEqual(again.sent, 0)         # its blobs were already there
        self.assertEqual(read(os.path.join(www, "manifest.json")), read(os.path.join(self.store, "manifest.json")))

    def test_a_deploy_stopped_at_once_sends_nothing(self):
        www = self.p("www")
        deploy.deploy(self.store, deploy.FolderTarget(www))
        blobs = sorted(os.listdir(os.path.join(www, "blobs")))
        self.release_two()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(deploy.DeployError) as caught:
            deploy.deploy(self.store, deploy.FolderTarget(www), cancel=stop)
        self.assertIn("before anything was sent", str(caught.exception))
        self.assertEqual(sorted(os.listdir(os.path.join(www, "blobs"))), blobs)

    def test_a_publish_stopped_leaves_the_store_as_it_was(self):
        before = read(os.path.join(self.store, "manifest.json"))
        write(self.p("src", "readme.txt"), "release two\n")
        stop = threading.Event()
        stop.set()
        with self.assertRaises(publish.PublishError):
            publish.publish(self.src, self.store, "Game", "2.0", cancel=stop)
        self.assertEqual(read(os.path.join(self.store, "manifest.json")), before)
        self.assertFalse(os.path.exists(os.path.join(self.store, "manifest.json.tmp")))


if __name__ == "__main__":
    unittest.main()
