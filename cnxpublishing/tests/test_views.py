# -*- coding: utf-8 -*-
# ###
# Copyright (c) 2013, Rice University
# This software is subject to the provisions of the GNU Affero General
# Public License version 3 (AGPLv3).
# See LICENCE.txt for details.
# ###
import os
import tempfile
import json
import shutil
import unittest
import zipfile

import psycopg2
from webob import Request
from webtest import TestApp
from pyramid import testing
from pyramid import httpexceptions

from .testing import (
    integration_test_settings,
    db_connection_factory,
    )


here = os.path.abspath(os.path.dirname(__file__))
TEST_DATA_DIR = os.path.join(here, 'data')


class PublishViewTestCase(unittest.TestCase):

    def setUp(self):
        self.config = testing.setUp()

    def tearDown(self):
        testing.tearDown()

    def test_epub_format_exception(self):
        """Test that we have a way to immediately fail if the EPUB
        is a valid EPUB structure. And all the files specified within
        the manifest and OPF documents.
        """
        post_data = {'epub': ('book.epub', b'')}
        request = Request.blank('/publications', POST=post_data)

        from ..views import publish
        with self.assertRaises(httpexceptions.HTTPBadRequest) as caught_exc:
            publish(request)

        exc = caught_exc.exception
        self.assertEqual(exc.args, ('Format not recognized.',))


class EPUBMixInTestCase(object):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)

    def pack_epub(self, directory):
        """Given an directory containing epub contents,
        pack it up and make return filepath.
        Packed file is remove on test exit.
        """
        zip_fd, zip_filepath = tempfile.mkstemp('.epub', dir=self.tmpdir)
        with zipfile.ZipFile(zip_filepath, 'w') as zippy:
            base_path = os.path.abspath(directory)
            for root, dirs, filenames in os.walk(directory):
                # Strip the absolute path
                archive_path = os.path.abspath(root)[len(base_path):]
                for filename in filenames:
                    filepath = os.path.join(root, filename)
                    archival_filepath = os.path.join(archive_path, filename)
                    zippy.write(filepath, archival_filepath)
        return zip_filepath

    def copy(self, src, dst_name='book'):
        """Convenient method for copying test data directories."""
        dst = os.path.join(self.tmpdir, dst_name)
        shutil.copytree(src, dst)
        return dst


class FunctionalViewTestCase(unittest.TestCase, EPUBMixInTestCase):
    """Request/response client interaction"""

    settings = None
    db_conn_str = None
    db_connect = None

    @property
    def api_keys_by_uid(self):
        """Mapping of uid to api key."""
        attr_name = '_api_keys'
        api_keys = getattr(self, attr_name, None)
        if api_keys is None:
            self.addCleanup(delattr, self, attr_name)
            from ..main import _parse_api_key_lines
            api_keys = _parse_api_key_lines(self.settings)
            setattr(self, attr_name, api_keys)
        return {x[1]:x[0] for x in api_keys}

    @classmethod
    def setUpClass(cls):
        cls.settings = integration_test_settings()
        from ..config import CONNECTION_STRING
        cls.db_conn_str = cls.settings[CONNECTION_STRING]
        cls.db_connect = staticmethod(db_connection_factory())
        cls._app = cls.make_app(cls.settings)

    @staticmethod
    def make_app(settings):
        from ..main import main
        app = main({}, **settings)
        return app

    @property
    def app(self):
        return TestApp(self._app)

    def setUp(self):
        EPUBMixInTestCase.setUp(self)
        config = testing.setUp(settings=self.settings)
        from cnxarchive.database import initdb
        initdb({'db-connection-string': self.db_conn_str})
        from ..db import initdb
        initdb(self.db_conn_str)

    def tearDown(self):
        with psycopg2.connect(self.db_conn_str) as db_conn:
            with db_conn.cursor() as cursor:
                cursor.execute("DROP SCHEMA public CASCADE")
                cursor.execute("CREATE SCHEMA public")
        testing.tearDown()

    def _accept_all_pending(self):
        """Accept all roles on all pending documents"""
        with self.db_connect() as db_conn:
            with db_conn.cursor() as cursor:
                cursor.execute("""\
                UPDATE pending_documents
                SET ("license_accepted", "roles_accepted") = ('t', 't')
                """)

    def test_loose_document_submission_to_publication(self):
        """\
        Publish a set of loose documents (all new documents).

        1. Submit an EPUB containing loose documents, not bound to a binder.
        2. Accept license and roles. [HACKED]
        3. Check the state of the publication.
        4. Verify documents are in the archive. [HACKED]
        """
        api_key = self.api_keys_by_uid['no-trust']

        # 1. --
        epub_directory = os.path.join(TEST_DATA_DIR, 'loose-pages')
        epub_filepath = self.pack_epub(epub_directory)
        upload_files = [('epub', epub_filepath,)]
        resp = self.app.post('/publications', upload_files=upload_files,
                             headers=[('x-api-key', api_key,)])
        self.assertEqual(resp.json['state'], 'Processing')
        publication_id = resp.json['publication']

        # 2. (manual) Accept license and roles for Figgy Pudd'n
        with self.db_connect() as db_conn:
            with db_conn.cursor() as cursor:
                cursor.execute("""\
                UPDATE pending_documents
                SET ("license_accepted", "roles_accepted") = ('t', 't')
                WHERE metadata->>'title' LIKE '%Figgy%'
                """)

        # 3. --
        path = "/publications/{}".format(publication_id)
        resp = self.app.get(path, headers=[('x-api-key', api_key,)])
        self.assertEqual(resp.json['state'], 'Processing')

        # 2. (manual)
        self._accept_all_pending()
        with self.db_connect() as db_conn:
            with db_conn.cursor() as cursor:
                # Typically, acceptance requests would poke the publication
                # into changing state.
                from ..db import poke_publication_state
                poke_publication_state(publication_id, current_state='Processing')

        # 3. --
        path = "/publications/{}".format(publication_id)
        resp = self.app.get(path, headers=[('x-api-key', api_key,)])
        self.assertEqual(resp.json['state'], 'Done/Success')

        # 4. (manual)
        with self.db_connect() as db_conn:
            with db_conn.cursor() as cursor:
                cursor.execute("SELECT name FROM modules ORDER BY name ASC")
                names = [row[0] for row in cursor.fetchall()]
        self.assertEqual(names, ["Boom", "Figgy Pudd'n"])

    def test_binder_submission_to_publication(self):
        """\
        Publish a binder with complete documents (all new documents).

        1. Submit an EPUB containing.
        2. Accept license and roles. [HACKED]
        3. Check the state of the publication.
        4. Verify binder and documents are in the archive. [HACKED]
        """
        api_key = self.api_keys_by_uid['no-trust']

        # 1. --
        epub_directory = os.path.join(TEST_DATA_DIR, 'book')
        epub_filepath = self.pack_epub(epub_directory)
        upload_files = [('epub', epub_filepath,)]
        resp = self.app.post('/publications', upload_files=upload_files,
                             headers=[('x-api-key', api_key,)])
        self.assertEqual(resp.json['state'], 'Processing')
        publication_id = resp.json['publication']

        # 2. (manual)
        self._accept_all_pending()
        with self.db_connect() as db_conn:
            with db_conn.cursor() as cursor:
                # Typically, acceptance requests would poke the publication
                # into changing state.
                from ..db import poke_publication_state
                poke_publication_state(publication_id, current_state='Processing')

        # 3. --
        path = "/publications/{}".format(publication_id)
        resp = self.app.get(path, headers=[('x-api-key', api_key,)])
        self.assertEqual(resp.json['state'], 'Done/Success')

        # 4. (manual)
        with self.db_connect() as db_conn:
            with db_conn.cursor() as cursor:
                cursor.execute("SELECT name FROM modules ORDER BY name ASC")
                names = [row[0] for row in cursor.fetchall()]
                self.assertEqual(
                    ['Book of Infinity', 'Document One of Infinity'],
                    names)

                cursor.execute("""\
SELECT portal_type, uuid||'@'||concat_ws('.',major_version,minor_version)
FROM modules""")
                items = dict(cursor.fetchall())
                document_ident_hash = items['Module']
                binder_ident_hash = items['Collection']

                expected_tree = {
                    "id": binder_ident_hash,
                    "title": "Book of Infinity",
                    "contents": [
                        {"id":"subcol",
                         "title":"Part One",
                         "contents":[
                             {"id":"subcol",
                              "title":"Chapter One",
                              "contents":[
                                  {"id": document_ident_hash,
                                   "title":"Document One"}]}]}]}
                cursor.execute("""\
SELECT tree_to_json(uuid::text, concat_ws('.',major_version, minor_version))
FROM modules
WHERE portal_type = 'Collection'""")
                tree = json.loads(cursor.fetchone()[0])

                self.assertEqual(expected_tree, tree)

    def test_loose_document_submission_to_publication_w_trust(self):
        """\
        Publish a set of loose documents (all new documents) using a trusted
        application relationship.

        Reminder: In a trusted relationship, the submitting application/user
        is has done license and role acceptance; and so we trust the publication
        no longer needs role and license acceptance and can be published
        immediately too the archive.

        1. Submit an EPUB containing loose documents, not bound to a binder.
        2. Check the state of the publication.
        3. Verify documents are in the archive. [HACKED]
        """
        api_key = self.api_keys_by_uid['some-trust']

        # 1. --
        epub_directory = os.path.join(TEST_DATA_DIR, 'loose-pages')
        epub_filepath = self.pack_epub(epub_directory)
        upload_files = [('epub', epub_filepath,)]
        resp = self.app.post('/publications', upload_files=upload_files,
                             headers=[('x-api-key', api_key,)])
        self.assertEqual(resp.json['state'], 'Done/Success')
        publication_id = resp.json['publication']

        # 2. --
        path = "/publications/{}".format(publication_id)
        resp = self.app.get(path, headers=[('x-api-key', api_key,)])
        self.assertEqual(resp.json['state'], 'Done/Success')

        # 3. (manual)
        with self.db_connect() as db_conn:
            with db_conn.cursor() as cursor:
                cursor.execute("SELECT name FROM modules ORDER BY name ASC")
                names = [row[0] for row in cursor.fetchall()]
        self.assertEqual(names, ["Boom", "Figgy Pudd'n"])

    def test_license_acceptance_to_publication(self):
        """\
        Accept the license to to-be-published documents.

        1. Submit an EPUB containing loose documents.
        2. Check the state of the publication.
        3. As a user, accept the license.
        4. Check the state of the license acceptance record.
        """
        api_key = self.api_keys_by_uid['no-trust']

        # 1. --
        epub_directory = os.path.join(TEST_DATA_DIR, 'loose-pages')
        epub_filepath = self.pack_epub(epub_directory)
        upload_files = [('epub', epub_filepath,)]
        resp = self.app.post('/publications', upload_files=upload_files,
                             headers=[('x-api-key', api_key,)])
        self.assertEqual(resp.json['state'], 'Processing')
        publication_id = resp.json['publication']

        # 2. --
        path = "/publications/{}".format(publication_id)
        resp = self.app.get(path, headers=[('x-api-key', api_key,)])
        self.assertEqual(resp.json['state'], 'Processing')

        # 3. --
        uid = 'charrose'
        path = '/publications/{}/license-acceptances/{}' \
            .format(publication_id, uid)
        resp = self.app.post(path, params={'accept-all': '1'})

        # 4. (manual)
        with self.db_connect() as db_conn:
            with db_conn.cursor() as cursor:
                cursor.execute("""\
SELECT acceptance
FROM publications_license_acceptance
WHERE user_id = %s
ORDER BY uuid ASC""", (uid,))
                accepted = cursor.fetchall()
        self.assertEqual(accepted, [(True,), (True,)])
