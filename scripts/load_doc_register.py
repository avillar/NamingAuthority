# from __future__ import print_function
import json
import logging
import argparse
import re
import sys
from typing import Union, Optional, List

import rdflib
from rdflib import Graph
from rdflib.namespace import DC, DCTERMS, SKOS, OWL, RDF, RDFS, XSD, DCAT
from pyld import jsonld
import jq
from os import path
from jsonpath_ng.ext import parse as jsonpathparse


def init_graph() -> Graph:
    g = rdflib.Graph()
    g.bind("dc", DC)
    g.bind("xsd", XSD)
    g.bind("dct", DCTERMS)
    g.bind("skos", SKOS)
    g.bind("owl", OWL)
    g.bind("rdf", RDF)
    g.bind("rdfs", RDFS)
    g.bind("dcat", DCAT)
    g.bind("iso", 'http://iso.org/tc211/')
    g.bind("spec", "http://www.opengis.net/def/ont/modspec/")
    g.bind("specrel", "http://www.opengis.net/def/ont/specrel/")
    g.bind("na", "http://www.opengis.net/def/metamodel/ogc-na/")
    g.bind("prov", "http://www.w3.org/ns/prov#")
    return g


def process_input(data: dict, contextfn: str):
    # Load YAML context file
    from yaml import load
    try:
        from yaml import CLoader as Loader
    except ImportError:
        from yaml import Loader
    with open(contextfn, 'r') as f:
        context = load(f, Loader=Loader)

    # Check if pre-transform necessary
    transform = context.get('transform')
    if transform:
        data = json.loads(jq.compile(transform).input(data).text())

    # Add contexts
    context_list = context.get('context', {})
    global_context = None
    for loc, val in context_list.items():
        if not loc or loc in ['.', '$']:
            global_context = val
        else:
            items = jsonpathparse(loc).find(data)
            for item in items:
                item.value['@context'] = val

    if global_context:
        data = {
            '@context': global_context,
            '@graph': data,
        }

    return data


def generate_graph(inputfn: str, contextfn: str, base: Optional[str] = None):

    g = init_graph()

    jsonld.set_document_loader(jsonld.requests_document_loader(timeout=5000))
    with open(inputfn, 'r') as j:
        inputdata = json.load(j)

    jdocld = process_input(inputdata, contextfn)

    options = {}
    if base:
        options['base'] = base
    output = json.dumps(jsonld.expand(jdocld, options), indent=2)
    g.parse(data=output, format='json-ld')

    return g, output


def process(inputfn: str,
            jsonldfn: Optional[Union[bool, str]] = False,
            ttlfn: Optional[Union[bool, str]] = False,
            contextfn: Optional[str] = None,
            base: Optional[str] = None,
            skip_on_missing_context: bool = False) -> List[str]:

    if not path.isfile(inputfn):
        raise IOError(f'Input is not a file ({inputfn})')

    inputbase, inputext = path.splitext(inputfn)

    if not contextfn:
        # Autodiscover context
        for cfn in [
            f'{inputfn}.yml',
            f'{inputfn}.yaml',
            f'{inputbase}.yaml',
            f'{inputbase}.yml',
        ]:
            if path.isfile(cfn):
                contextfn = cfn
                break
    if not contextfn:
        if skip_on_missing_context:
            logging.warning("No context file provided and one could not be discovered automatically. Skipping...")
            return []
        raise Exception('No context file provided and one could not be discovered automatically')

    g, jsonlddoc = generate_graph(inputfn, contextfn, base)

    createdfiles = []
    # False = do not generate
    # None = auto filename
    # - = stdout
    if ttlfn or ttlfn is None:
        if ttlfn == '-':
            print(g.serialize(format='ttl'))
        else:
            if not ttlfn:
                ttlfn = f'{inputbase}.ttl' if inputext != '.ttl' else f'{inputfn}.ttl'
            g.serialize(destination=ttlfn, format='ttl')
            createdfiles.append(ttlfn)

    # False = do not generate
    # None = auto filename
    # "-" = stdout
    if jsonldfn or jsonldfn is None:
        if jsonldfn == '-':
            print(jsonlddoc)
        else:
            if not jsonldfn:
                jsonldfn = f'{inputbase}.jsonld' if inputext != '.jsonld' else f'{inputfn}.jsonld'
            with open(jsonldfn, 'w') as f:
                f.write(jsonlddoc)
            createdfiles.append(jsonldfn)

    return createdfiles


if __name__ == '__main__':

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "input",
        help="Source file (instead of service)",
    )

    parser.add_argument(
        '-j',
        '--json-ld',
        action='store_true',
        help="Generate JSON-LD output file",
    )

    parser.add_argument(
        '--json-ld-file',
        help='JSON-LD output filename',
    )

    parser.add_argument(
        '-t',
        '--ttl',
        action='store_true',
        help='Generate TTL output file',
    )

    parser.add_argument(
        "--ttl-file",
        help="TTL output filename",
    )

    parser.add_argument(
        '-c',
        '--context',
        help='YAML context file (instead of autodetection)',
    )

    parser.add_argument(
        '-b',
        '--base-uri',
        help='Base URI for JSON-LD',
    )

    parser.add_argument(
        '-s',
        '--skip-on-missing-context',
        help='Skip files for which a context definition cannot be found (instead of failing)',
    )

    parser.add_argument(
        '--batch',
        help='Batch processing where input file is one or more files separated by commas, context files are '
             'autodiscovered and output file names are always auto generated',
        action='store_true'
    )

    parser.add_argument(
        '--fs',
        help='File separator for formatting list of output files (no output by default)',
    )

    args = parser.parse_args()

    outputfiles = []
    if args.batch:
        print("Input files: {}".format(args.input), file=sys.stderr)
        for fn in args.input.split(','):
            if not re.match(r'.*\.json-?(ld)?$', fn):
                print('File {} does not match, skipping'.format(fn), file=sys.stderr)
                continue
            print('File {} does matches, processing'.format(fn), file=sys.stderr)
            try:
                outputfiles += process(
                    fn,
                    jsonldfn=None if args.json_ld else False,
                    ttlfn=None if args.ttl else False,
                    contextfn=None,
                    base=args.base_uri,
                    skip_on_missing_context=True
                )
            except Exception as e:
                logging.warning("Error processing JSON/JSON-LD file, skipping: %s", str(e))
    else:
        outputfiles += process(args.input,
            jsonldfn=args.json_ld_file if args.json_ld else False,
            ttlfn=args.ttl_file if args.ttl else False,
            contextfn=args.context,
            base=args.base_uri,
            skip_on_missing_context=args.skip_on_missing_context,
        )

    if args.fs:
        print(args.fs.join(outputfiles))