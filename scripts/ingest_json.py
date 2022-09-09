# from __future__ import print_function
import json
import logging
import argparse
import re
import sys
from collections import deque
from typing import Union, Optional, List, Tuple

import rdflib
from rdflib import Graph
from rdflib.namespace import DC, DCTERMS, SKOS, OWL, RDF, RDFS, XSD, DCAT
from pyld import jsonld
import jq
from os import path, scandir
from jsonpath_ng.ext import parse as jsonpathparse

logger = logging.getLogger(__name__)

def init_graph() -> Graph:
    """
    Creates an empty graph with some standard prefixes.

    :return: an empty RDFLib Graph with some prefixes
    """

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


def process_input(data: dict, contextfn: str) -> dict:
    """
    Transform a JSON document loaded in a dict, and embed JSON-LD context into it.

    WARNING: This function modifies the input dict. If that is not desired, make a copy
    before invoking.

    :param data: the JSON document in dict format
    :param contextfn: YAML context definition filename
    :return: the transformed and JSON-LD-enriched data
    """

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


def generate_graph(inputfn: str, contextfn: str, base: Optional[str] = None) -> Tuple[Graph, str]:
    """
    Create a graph from an input JSON document and a YAML context definition file.

    :param inputfn: input filename
    :param contextfn: YAML context definition filename
    :param base: base URI for JSON-LD context
    :return: a tuple with the resulting RDFLib Graph and the JSON-LD enriched file
    """

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
            context_registry: Optional[dict] = None,
            base: Optional[str] = None,
            skip_on_missing_context: bool = False) -> List[str]:
    """
    Process input file and generate output RDF files.

    :param inputfn: input filename
    :param jsonldfn: output JSON-lD filename (None for automatic).
        If False, no JSON-LD output will be generated
    :param ttlfn: output Turtle filename (None for automatic).
        If False, no Turtle output will be generated.
    :param contextfn: YAML context filename. If None, will be autodetected:
        1. From a file with the same name but yml/yaml extension (test.json -> test.yml)
        2. From a _json-context.yml/_json-context.yaml file in the same directory
    :param context_registry: dict with filename:yamlContextFilename mappings. Will be ignored
        if contextfn is provided
    :param base: base URI for JSON-LD
    :param skip_on_missing_context: whether to silently fail if no context file is found
    :return: List of output files created
    """

    if not path.isfile(inputfn):
        raise IOError(f'Input is not a file ({inputfn})')

    inputbase, inputext = path.splitext(inputfn)

    if not contextfn:
        contextfn = find_context_filename(inputfn, context_registry)

    if not contextfn:
        if skip_on_missing_context:
            logger.warning("No context file provided and one could not be discovered automatically. Skipping...")
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


def find_context_filename(filename, registry: Optional[dict]) -> Optional[str]:
    """
    Find the YAML context file for a given filename, with the following precedence:
        1. Search in registry (if provided)
        2. Search file with same base name but with yaml/yml extension.
        3. Find _json-context.yml/yaml file in same directory
    :param filename: the filename for which to find the context
    :param registry: an optional filename:yamlContextFile mapping
    :return: the YAML context definition filename
    """

    # 1. Registry lookup
    if registry:
        for doc, yml in registry.items():
            if path.samefile(doc, filename):
                return yml

    # 2. Same filename with yml/yaml extension or autodetect in dir
    base, ext = path.splitext(filename)
    dirname = path.dirname(filename)

    for cfn in [
        f'{filename}.yml',
        f'{filename}.yaml',
        f'{base}.yaml',
        f'{base}.yml',
        path.join(dirname, '_json-context.yml'),
        path.join(dirname, '_json-context.yaml'),
    ]:
        if path.isfile(cfn):
            logger.info(f'Autodetected context {cfn} for file {filename}')
            return cfn


def filenames_from_context(contextfn: str, registry: Optional[dict]) -> Optional[List[str]]:
    """
    Tries to find a JSON/JSON-LD file from a given YAML context definition filename.
    Priority:
      1. Context file in registry (if registry present)
      2. Context file with same name as JSON doc (e.g. test.yml/test.json)
      3. Context file in directory (_json-context.yml or _json-context.yaml)
    :param contextfn: YAML context definition filename
    :param registry: dict of jsonFile:yamlContextFile mappings
    :return: corresponding JSON/JSON-LD filename, if found
    """

    # 1. Reverse lookup in registry
    if registry:
        found = [k for k, v in registry.items() if path.samefile(v, contextfn)]
        if found:
            return found

    # 2. Lookup by matching filename
    basefn = path.splitext(contextfn)[0]
    if re.match(r'.*\.json-?(ld)?$', basefn):
        # If removing extension results in a JSON/JSON-LD
        # filename, try it
        return basefn if basefn not in registry and path.isfile(basefn) else None
    # Otherwise check with appended JSON/JSON-LD extensions
    for e in ('.json', '.jsonld', '.json-ld'):
        if basefn not in registry and path.isfile(basefn + e):
            return fn

    # 3. If directory context file, all .json files in directory
    # NOTE: no .jsonld or .json-ld files, since those could come
    #   from the output of this very script
    # NOTE: excluding those files present in the registry
    dirname, ctxfn = path.split(contextfn)
    if re.match(r'_json-context\.ya?ml', ctxfn):
        with scandir(dirname) as it:
            return [x.path for x in it
                    if (x.is_file() and x.name.endswith('.json')
                        and not any(path.samefile(x, y) for y in registry))]


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

    parser.add_argument(
        '-r',
        '--context-registry',
        help='JSON context registry file containing an object of jsonFile:yamlContextFile pairs'
    )

    args = parser.parse_args()

    context_registry = {}
    if args.context_registry:
        regdir = path.dirname(args.context_registry)
        norm = lambda x: path.abspath(path.normpath(path.join(regdir, x)))
        with open(args.context_registry, 'r') as f:
            context_registry = {norm(doc): norm(yml) for doc, yml in json.load(f).items()}

    outputfiles = []
    if args.batch:
        logger.info("Input files: {}".format(args.input))
        remaining_fn: deque = deque(args.input.split(','))
        while remaining_fn:
            fn = remaining_fn.popleft()

            if re.match(r'.*\.ya?ml$', fn):
                # Context file found, try to find corresponding JSON/JSON-LD file(s)
                remaining_fn.extend(filenames_from_context(fn, context_registry))
                continue

            if not re.match(r'.*\.json-?(ld)?$', fn):
                logger.debug('File %s does not match, skipping', fn)
                continue
            logger.info('File %s matches, processing', fn)
            try:
                outputfiles += process(
                    fn,
                    jsonldfn=None if args.json_ld else False,
                    ttlfn=None if args.ttl else False,
                    contextfn=None,
                    context_registry=context_registry,
                    base=args.base_uri,
                    skip_on_missing_context=True
                )
            except Exception as e:
                logger.warning("Error processing JSON/JSON-LD file, skipping: %s", str(e))
    else:
        outputfiles += process(args.input,
            jsonldfn=args.json_ld_file if args.json_ld else False,
            ttlfn=args.ttl_file if args.ttl else False,
            contextfn=args.context,
            context_registry=context_registry,
            base=args.base_uri,
            skip_on_missing_context=args.skip_on_missing_context,
        )

    if args.fs:
        print(args.fs.join(outputfiles))