5# -*- coding: utf-8 -*-
# ###
# Copyright (c) 2013, Rice University
# This software is subject to the provisions of the GNU Affero General
# Public License version 3 (AGPLv3).
# See LICENCE.txt for details.
# ###
import cnxepub
import psycopg2
from pyramid.view import view_config
from pyramid import httpexceptions

from . import config
from .db import (
    add_publication,
    poke_publication_state,
    check_publication_state,
    )


@view_config(route_name='publications', request_method='POST', renderer='json',
             permission='publish')
def publish(request):
    """Accept a publication request at form value 'epub'"""
    if 'epub' not in request.POST:
        raise httpexceptions.HTTPBadRequest("Missing EPUB in POST body.")

    epub_upload = request.POST['epub']
    try:
        epub = cnxepub.EPUB.from_file(epub_upload.file)
    except:
        raise httpexceptions.HTTPBadRequest('Format not recognized.')

    settings = request.registry.settings
    # Make a publication entry in the database for status checking
    # the publication. This also creates publication entries for all
    # of the content in the EPUB.
    with psycopg2.connect(settings[config.CONNECTION_STRING]) as db_conn:
        with db_conn.cursor() as cursor:
            publication_id, publications = add_publication(cursor,
                                                           epub,
                                                           epub_upload.file)

    # Poke at the publication & lookup its state.
    state = poke_publication_state(publication_id)

    response_data = {
        'publication': publication_id,
        'mapping': publications,
        'state': state,
        }
    return response_data


@view_config(route_name='get-publication', request_method=['GET', 'HEAD'],
             renderer='json', permission='view')
def get_publication(request):
    """Lookup publication state"""
    publication_id = request.matchdict['id']
    state = check_publication_state(publication_id)
    response_data = {
        'publication': publication_id,
        'state': state,
        }
    return response_data


@view_config(route_name='license-acceptance', request_method='GET',
             renderer='templates/license-acceptances.jinja2')
def get_accept_license(request):
    """This produces an HTML form for accepting the license."""
    publication_id = request.matchdict['id']
    user_id = request.matchdict['uid']
    setting = request.registry.settings

    # TODO Verify the accepting user is the one making the request.

    # For each pending document, accept the license.
    with psycopg2.connect(settings[config.CONNECTION_STRING]) as db_conn:
        with db_conn.cursor() as cursor:
            cursor.execute("""
SELECT
  pd.uuid||'@'||concat_ws('.', pd.major_version, pd.minor_version) AS ident_hash,
  license_accepted
FROM
  pending_documents AS pd
  NATURAL JOIN publications_license_acceptance AS pla
WHERE pd.publication_id = %s AND user_id = %s""",
                           (publication_id, user_id))
            user_document_acceptances = cursor.fetchall()

    return {'publication_id': publication_id,
            'user_id': user_id,
            'document_acceptances': user_document_acceptances,
            }


@view_config(route_name='license-acceptance', request_method='POST')
def post_accept_license(request):
    """Accept license acceptance requests."""
    publication_id = request.matchdict['id']
    uid = request.matchdict['uid']
    settings = request.registry.settings

    form_key = 'accept-all'
    has_accepted_all = request.params.get(form_key, 0)
    try:
        has_accepted_all = bool(int(has_accepted_all))
    except:
        raise httpexceptions.HTTPBadRequest(
            "Invalid value for {}.".format(form_key))

    # TODO Verify the accepting user is the one making the request.
    # They could be authenticated but not be the license acceptor.

    # For each pending document, accept the license.
    with psycopg2.connect(settings[config.CONNECTION_STRING]) as db_conn:
        with db_conn.cursor() as cursor:
               cursor.execute("""\
UPDATE publications_license_acceptance AS pla
SET acceptance = 't'
FROM pending_documents AS pd
WHERE
  pd.publication_id = %s
  AND
  pla.user_id = %s
  AND
  pd.uuid = pla.uuid""",
                              (publication_id, uid))
    state = poke_publication_state(publication_id)
    location = request.route_url('license-acceptance',
                                 id=publication_id, uid=uid)
    return httpexceptions.HTTPFound(location=location)


@view_config(route_name='role-acceptance', request_method='GET',
             renderer='templates/role-acceptances.jinja2')
def get_accept_role(request):
    """This produces an HTML form for accepting the license."""
    publication_id = request.matchdict['id']
    setting = request.registry.settings

    # TODO Verify the accepting user making the request
    # is an already vetted role.

    # For each pending document, accept the license.
    with psycopg2.connect(settings[config.CONNECTION_STRING]) as db_conn:
        with db_conn.cursor() as cursor:
            cursor.execute("""
SELECT row_to_json(combined_rows) FROM (
SELECT
  pd.uuid||'@'||concat_ws('.', pd.major_version, pd.minor_version) AS ident_hash,
  pra.user_id AS user_id,
  pra.acceptance AS accepted
FROM
  pending_documents AS pd
  NATURAL JOIN publications_role_acceptance AS pra
WHERE pd.publication_id = %s
) AS combined_rows""",
                           (publication_id, user_id))
            role_acceptances = cursor.fetchall()

    return {'publication_id': publication_id,
            'role_acceptances': role_acceptances,
            }


@view_config(route_name='role-acceptance', request_method='POST')
def post_accept_role(request):
    """Accept role acceptance requests."""
    publication_id = request.matchdict['id']
    settings = request.registry.settings

    form_key = 'accept-all'
    has_accepted_all = request.params.get(form_key, 0)
    try:
        has_accepted_all = bool(int(has_accepted_all))
    except:
        raise httpexceptions.HTTPBadRequest(
            "Invalid value for {}.".format(form_key))

    # TODO Verify the accepting user making the request
    # is an already vetted role..
    # They could be authenticated but not be the vetted.

    # For each document, accept the role.
    with psycopg2.connect(settings[config.CONNECTION_STRING]) as db_conn:
        with db_conn.cursor() as cursor:
               cursor.execute("""\
UPDATE publications_role_acceptance AS pra
SET acceptance = 't'
FROM pending_documents AS pd
WHERE
  pd.publication_id = %s
  AND
  pd.uuid = pra.uuid""",
                              (publication_id,))
    state = poke_publication_state(publication_id)
    location = request.route_url('role-acceptance',
                                 id=publication_id)
    return httpexceptions.HTTPFound(location=location)
