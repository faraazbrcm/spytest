################################################################################
#                                                                              #
#  Copyright 2021 Broadcom. The term Broadcom refers to Broadcom Inc. and/or   #
#  its subsidiaries.                                                           #
#                                                                              #
#  Licensed under the Apache License, Version 2.0 (the "License");             #
#  you may not use this file except in compliance with the License.            #
#  You may obtain a copy of the License at                                     #
#                                                                              #
#     http://www.apache.org/licenses/LICENSE-2.0                               #
#                                                                              #
#  Unless required by applicable law or agreed to in writing, software         #
#  distributed under the License is distributed on an "AS IS" BASIS,           #
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.    #
#  See the License for the specific language governing permissions and         #
#  limitations under the License.                                              #
#                                                                              #
################################################################################

from genericpath import exists
import optparse
import sys
import os, copy
from pyang import plugin
from pyang import statements
from collections import OrderedDict
from jinja2 import Environment, FileSystemLoader
import keyword
import pdb

from regex.regex import split

# globals
mod_dict = OrderedDict()
class_dict = OrderedDict()
class_name_dict = OrderedDict()

base_class_template = "base.j2"

derived_class_template = "derived.j2"

def pyang_plugin_init():
    plugin.register_plugin(SPyTestPlugin())

class SPyTestPlugin(plugin.PyangPlugin):
    def add_output_format(self, fmts):
        self.multiple_modules = True
        fmts['spytest'] = self

    def add_opts(self, optparser):
        optlist = [
            optparse.make_option("--out-dir",
                                 type="string",
                                 dest="out_dir",
                                 help="Output directory for message classes"),
            optparse.make_option("--template-dir",
                                 type="string",
                                 dest="template_dir",
                                 help="Directory containing templates for Codegen")                                 
        ]
        g = optparser.add_option_group("SPyTestPlugin options")
        g.add_options(optlist)

    def setup_fmt(self, ctx):
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        global currentModule

        if not ctx.opts.out_dir:
            os.makedirs(ctx.opts.out_dir, exist_ok=True)

        if ctx.opts.template_dir and not os.path.exists(ctx.opts.template_dir):
            print("[Error]: Specified --template-dir: ",
                  ctx.opts.template_dir, " does not exists")
            sys.exit(1)

        # Builds unique mod name
        # This will be used to generate top-level directory
        # Example: acl/ network_instances/ openconfig_interfaces/ ...
        for module in modules:
            #we dont care submods, as mainmods will have their the contents
            if module.keyword == "submodule":
                continue
            build_mod_dict(module)

        for mod in mod_dict:
            module = mod_dict[mod]
            print("===> processing {} ...".format(module.i_modulename))
            reset()
            walk_module(module)        
            # formats the class names
            mk_obj_name(class_dict, class_name_dict)
            # formats the attribute names
            for cls in class_dict:
                mk_obj_name(class_dict[cls]["attrs"], class_dict[cls]["attr_name_dict"])
            
            cls_del = []
            for cls in class_dict:
                # Delete Empty class
                if (class_dict[cls]["keyword"] == "container" or \
                    class_dict[cls]["keyword"] == "module") and \
                    (len(class_dict[cls]["attrs"].keys()) == 0):
                    for child in class_dict[cls]["children"]:
                        class_dict[child]["parent"] = None
                    cls_del.append(cls)
            for cls in cls_del:
                del(class_dict[cls])
            for cls in class_dict:
                templateEnv = Environment(loader=FileSystemLoader(ctx.opts.template_dir), 
                    trim_blocks=True, lstrip_blocks=True, extensions=['jinja2.ext.do', 'jinja2.ext.loopcontrols'])
                base_class = templateEnv.get_template(base_class_template).render(safe_name=safe_name, cls=cls, class_dict=class_dict, class_name_dict=class_name_dict)
                out_dir = os.path.join(ctx.opts.out_dir, mod)
                out_dir_base = os.path.join(out_dir, 'Base') 
                os.makedirs(out_dir_base, exist_ok=True)
                base_class_name = class_name_dict[cls]["name"] + ".py"
                base_class_file = os.path.join(out_dir_base, base_class_name)
                with open(base_class_file, "w") as fp:
                    fp.write(base_class)

def reset():
    global class_name_dict, class_dict
    class_dict = OrderedDict()
    class_name_dict = OrderedDict()

def mk_obj_name(data_dict, name_dict):
    for cls in data_dict:
        path_elements = (list(filter(None,cls.split('/'))))
        #Remove prefixes
        path_elements = list(map(lambda elem : elem.split(':')[1] if ':' in elem else elem, path_elements))
        all_names =  data_dict.keys()
        match = True
        index = -1
        chosen_name = ''
        while match:
            chosen_name = '/' + "/".join(path_elements[index:])
            for c_index, name in enumerate(all_names):
                if name == cls:
                    if c_index == len(all_names)-1:
                        match = False                    
                    continue # avoid comparing with self
                name_elements = (list(filter(None,name.split('/'))))
                cls_name = "/" + "/".join(list(map(lambda elem : elem.split(':')[1] if ':' in elem else elem, name_elements)))
                #cls_name = "/" + "/".join(name_elements)
                if cls_name.endswith(chosen_name):
                    break
                if c_index == len(all_names)-1:
                    match = False
            if match:
                if index + len(path_elements) == -1:
                    print("Unable to autogenerate name for {}, please provide an unique name using codegen extensions".format(cls))
                    match = False
                    #sys.exit(2)
                index = index -1
        if index == -1:
            chosen_name = path_elements[index]
        else:
            chosen_name = chosen_name.split('/')[1] + '_' + path_elements[-1]
        chosen_name = getCamelForm(chosen_name.replace('/','').replace('-','_'))
        chosen_name = safe_name(chosen_name)
        name_dict[cls] = {
            "name": chosen_name,
            "elements": path_elements,
            "elements_safe": list(map(lambda elem: safe_name(elem), path_elements))
        }

def get_description(node):
    desc = node.search_one('description')
    if desc:
        return desc.arg
    else:
        "Description not present in YANG"

def get_config_string(node):
    if hasattr(node, 'i_config'):
        if node.i_config:
            return "config"
        else:
            return "state"
    else:
        return "N/A"

def build_class_dict(child):
    class_name = mk_path_refine(child)
    if child.keyword == "module" or child.keyword == "submodule":
        module_name = child.i_modulename
    else:
        module_name = child.i_module.i_modulename
    class_dict[class_name] = {
        "attrs": OrderedDict(), 
        "parent": None, 
        "children": [],
        "keyword": child.keyword, 
        "yang_name":child.arg,
        "module_name": module_name,
        "module_name_safe": safe_name(module_name),
        "yang_name_safe": safe_name(child.arg),
        "yang_path": mk_path_refine(child), 
        "node": child,
        "rest_path": mk_path_refine(child, "rest"),
        "gnmi_path": mk_path_refine(child, "gnmi"),
        "config": get_config_string(child),
        "keys": [],
        "attr_name_dict": OrderedDict(),
        "description": get_description(child)
    }
    return class_name

def walk_module(module):
    for child in module.i_children:
        walk_child(child, None)

def walk_child(child, class_name):
    if class_name is None and child.keyword == "container":
        # This case will hit when No class is set and the
        # first container is hit
        class_name = build_class_dict(child)
    elif child.keyword == "list":
        parent_class = class_name
        class_name = build_class_dict(child)
        for key in child.i_key:
            class_dict[class_name]["keys"].append(mk_path_refine(key))
        if parent_class is not None:
            class_dict[parent_class]["children"].append(class_name)
            #Associate parent with child
            class_dict[class_name]["parent"] = parent_class
    elif child.keyword == "leaf" or child.keyword == "leaf-list":
        # This case will hit when leaf/leaf-list 
        # are direct children of a module
        if class_name is None:
            class_name = build_class_dict(child.i_module)
        leaf_name = mk_path_refine(child)
        is_key = False
        if hasattr(child,'i_is_key'):
            is_key = True
        leaf_data = {
            "keyword": child.keyword,
            "is_key": is_key,
            "yang_name":child.arg,
            "module_name": child.i_module.i_modulename,
            "module_name_safe": safe_name(child.i_module.i_modulename),
            "yang_name_safe": safe_name(child.arg),            
            "yang_path": mk_path_refine(child),
            "rest_path": mk_path_refine(child, "rest"),
            "gnmi_path": mk_path_refine(child, "gnmi"),
            "node": child,
            "config": get_config_string(child),
            "description": get_description(child)
        }
        class_dict[class_name]["attrs"][leaf_name]= leaf_data

    if hasattr(child, 'i_children'):
        for ch in child.i_children:
            walk_child(ch, class_name)

def build_mod_dict(module):
    """
    Removes standard prefix from module name.
    If there exists a dupicate after removing a prefix, \
        the standard prefix will be brought in.
    """
    global mod_dict
    if module.arg.startswith("openconfig-"):
        arg = module.arg.replace("openconfig-","")
    if module.arg.startswith("ietf-"):
        arg = module.arg.replace("ietf-","")
    arg = arg.replace('-','_')
    if arg in mod_dict.keys():
        mod_instance = mod_dict[arg]
        mod_dict[mod_instance.arg.replace('-','_')] = mod_instance
        mod_dict[module.arg.replace('-','_')] = module
        del(mod_dict[arg])
    else:
        mod_dict[arg.replace('-','_')] = module

# Words that could turn up in YANG definition files that are actually
# reserved names in Python, such as being builtin types. This list is
# not complete, but will probably continue to grow.
reserved_name = ["list", "str", "int", "global", "decimal", "float",
                  "as", "if", "else", "elif", "map", "set", "class",
                  "from", "import", "pass", "return", "is", "exec",
                  "pop", "insert", "remove", "add", "delete", "local",
                  "get", "default", "yang_name", "def", "print", "del",
                  "break", "continue", "raise", "in", "assert", "while",
                  "for", "try", "finally", "with", "except", "lambda",
                  "or", "and", "not", "yield", "property", "min", "max"]

def safe_name(arg):
  """
    Make a leaf or container name safe for use in Python.
  """
  arg = arg.replace("-", "_")
  arg = arg.replace(".", "_")
  if arg in reserved_name:
    arg += "_"
  # store the unsafe->original version mapping
  # so that we can retrieve it when get() is called.
  return arg

def getCamelForm(moName):
    hasHiphen = False
    moName = moName.replace('_', '-')
    if '-' in moName:
        hasHiphen = True

    while (hasHiphen):
        index = moName.find('-')
        if index != -1:
            moNameList = list(moName)
            # capitalize character hiphen
            moNameList[index+1] = moNameList[index+1].upper()
            # delete '-'
            del(moNameList[index])
            moName = "".join(moNameList)

            if '-' in moName:
                hasHiphen = True
            else:
                hasHiphen = False
        else:
            break

    moName = moName[0].capitalize() + moName[1:]
    return moName


def mk_path_refine(node, ui=None):

    def mk_path(node, ui):
        """Returns the XPath of the node"""
        if node.keyword in ['choice', 'case']:
            return mk_path(node.parent, ui)

        def name(node):
            if node.keyword in ['module', 'submodule']:
                node_name = node.arg + ':' + node.arg
            else:
                node_name =  node.i_module.i_modulename + ':' + node.arg
            if node.keyword == "list":
                if ui == "rest":
                    node_name = "{}=".format(node_name)
                    for key in node.i_key:
                        node_name = node_name + "{},"
                    node_name = node_name.strip(',')
                    return node_name
                elif ui == "gnmi":
                    node_name = "{}".format(node_name)
                    for key in node.i_key:
                        node_name = node_name + "[{}=".format(key.arg) + "{}]"
                    node_name = node_name.strip(',')
                    return node_name
                else:
                    return node_name
            else:
                return node_name


        if node.keyword in ['module', 'submodule']:
            return "/" + name(node)

        if node.parent.keyword in ['module', 'submodule']:
            return "/" + name(node)
        else:
            p = mk_path(node.parent, ui)
            return p + "/" + name(node)

    xpath = mk_path(node, ui)
    module_name = ""
    final_xpathList = []
    for path in xpath.split('/')[1:]:
        mod_name, node_name = path.split(':')
        if mod_name != module_name:
            final_xpathList.append(path)
            module_name = mod_name
        else:
            final_xpathList.append(node_name)

    xpath = "/".join(final_xpathList)
    if not xpath.startswith('/'):
        xpath = '/' + xpath

    return xpath

