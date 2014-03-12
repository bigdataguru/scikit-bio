#!/usr/bin/env python

#-----------------------------------------------------------------------------
# Copyright (c) 2013, The BiPy Developers.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------

import re
from operator import or_
from random import shuffle
from copy import deepcopy
from itertools import combinations
from numpy import argsort, zeros
from bipy.maths.stats.test import correlation_t
from bipy.core.exception import (NoLengthError, DuplicateNodeError,
    NoParentError, MissingNodeError)

__credits__ = ["Gavin Huttley", "Peter Maxwell", "Rob Knight",
                    "Andrew Butterfield", "Catherine Lozupone", "Micah Hamady",
                    "Jeremy Widmann", "Zongzhi Liu", "Daniel McDonald",
                    "Justin Kuczynski"]

def distance_from_r(m1, m2):
    """Estimates distance as (1-r)/2: neg correl = max distance"""
    return (1-correlation_t(m1.flat, m2.flat)[0])/2

class TreeNode(object):
    _exclude_from_copy = set(['Parent', 'Children', '_node_cache'])

    def __init__(self, Name=None, Length=None, Parent=None):
        self.Name = Name
        self.Length = Length
        self.Parent = Parent
        self.Children = []
        self._node_cache = {}

    ### start operators ###
    def __repr__(self):
        """Returns summary of the tree"""
        nodes = [n for n in self.traverse(include_self=True)]
        n_tips = sum([n.is_tip() for n in nodes])
        n_nontips = len(nodes) - n_tips
        name = self.__class__.__name__

        return "<%s, Number of internal nodes: %d, Number of tips: %d>" % \
                (name, n_tips, n_nontips)

    def __str__(self):
        """Returns string version of self, with names and distances."""
        return self.to_newick(with_distances=True)

    def __iter__(self):
        """Node iter iterates over the Children."""
        return iter(self.Children)

    def __len__(self):
        return len(self.Children)

    def __getitem__(self, i):
        """Node delegates slicing to Children"""
        return self.Children[i]

    ### end operators ###

    ### start topology updates ###
    def _adopt(self, node):
        """Update parent references but does NOT update self.Children"""
        if node.Parent is not None:
            node.Parent.remove(node)
        node.Parent = self
        self.invalidate_node_cache()
        return node

    def append(self, node):
        """Appends i to self.Children, in-place, cleaning up refs."""
        self.Children.append(self._adopt(node))
        self.invalidate_node_cache()

    def extend(self, nodes):
        self.Children.extend(map(self._adopt, nodes))
        self.invalidate_node_cache()

    def pop(self, index=-1):
        self.invalidate_node_cache()
        return self._remove_node(index)

    def _remove_node(self, idx):
        self.invalidate_node_cache()
        node = self.Children.pop(idx)
        node.Parent = None
        return node

    def remove(self, node):
        for (i, curr_node) in enumerate(self.Children):
            if curr_node == node:
                self._remove_node(i)
                return True
        self.invalidate_node_cache()
        return False

    def remove_deleted(self, f):
        """Delete nodes in which f(node) evaluates True"""
        for node in self.traverse(include_self=False):
            if f(node):
                node.Parent.remove(node)

    def prune(self):
        """Reconstructs correct topology after nodes have been removed.

        Internal nodes with only one child will be removed and new connections
        will be made to reflect change.
        """
        # build up the list of nodes to remove so the topology is not altered
        # while traversing
        nodes_to_remove = []
        for node in self.traverse(include_self=False):
            if len(node.Children) == 1:
                nodes_to_remove.append(node)

        # clean up the single children nodes
        for node in nodes_to_remove:
            node.Parent.append(node.Children[0])
            node.Parent.remove(node)

        self.invalidate_node_cache()

    ### end topology updates ###

    ### copy like methods
    def copy(self, memo=None, _nil=[], constructor='ignored'):
        """Returns a copy of self using an iterative approach"""
        def __copy_node(n):
            result = n.__class__()
            efc = n._exclude_from_copy
            for k,v in n.__dict__.items():
                if k not in efc:
                    result.__dict__[k] = deepcopy(n.__dict__[k])
            return result

        root = __copy_node(self)
        nodes_stack = [[root, self, len(self.Children)]]

        while nodes_stack:
            #check the top node, any children left unvisited?
            top = nodes_stack[-1]
            new_top_node, old_top_node, unvisited_children = top

            if unvisited_children:
                top[2] -= 1
                old_child = old_top_node.Children[-unvisited_children]
                new_child = __copy_node(old_child)
                new_top_node.append(new_child)
                nodes_stack.append([new_child, old_child, \
                                    len(old_child.Children)])
            else:  #no unvisited children
                nodes_stack.pop()
        return root

    __deepcopy__ = deepcopy = copy

    def subtree(self, tip_list=None):
        """Make a copy of the subtree"""
        st = self.copy()
        raise NotImplementedError()


    def subset(self):
        """Returns set of names that descend from specified node"""
        return frozenset([i.Name for i in self.tips()])

    def subsets(self):
        """Returns all sets of names that come from self and its kids"""
        sets = []
        for i in self.postorder(include_self=False):
            if not i.Children:
                i.__leaf_set = frozenset([i.Name])
            else:
                leaf_set = reduce(or_, [c.__leaf_set for c in i.Children])
                if len(leaf_set) > 1:
                    sets.append(leaf_set)
                i.__leaf_set = leaf_set
        return frozenset(sets)

    def root_at(self, node):
        raise NotImplementedError()

    ### end copy like methods ###

    ### node checks ###

    def is_tip(self):
        """Returns True if the current node is a tip, i.e. has no children."""
        return not self.Children

    def is_root(self):
        """Returns True if the current is a root, i.e. has no parent."""
        return self.Parent is None

    ### end node checks ###

    ### traversal methods ###
    def traverse(self, self_before=True, self_after=False, include_self=True):
        """Returns iterator over descendants. Iterative: safe for large trees.

        self_before includes each node before its descendants if True.
        self_after includes each node after its descendants if True.
        include_self includes the initial node if True.

        self_before and self_after are independent. If neither is True, only
        terminal nodes will be returned.

        Note that if self is terminal, it will only be included once even if
        self_before and self_after are both True.

        This is a depth-first traversal. Since the trees are not binary,
        preorder and postorder traversals are possible, but inorder traversals
        would depend on the data in the tree and are not handled here.
        """
        if self_before:
            if self_after:
                return self.pre_and_postorder(include_self=include_self)
            else:
                return self.preorder(include_self=include_self)
        else:
            if self_after:
                return self.postorder(include_self=include_self)
            else:
                return self.tips(include_self=include_self)

    def preorder(self, include_self=True):
        """Performs preorder iteration over tree."""
        stack = [self]
        while stack:
            curr = stack.pop()
            if include_self or (curr is not self):
                yield curr
            if curr.Children:
                stack.extend(curr.Children[::-1])

    def postorder(self, include_self=True):
        """Performs postorder iteration over tree.

        This is somewhat inelegant compared to saving the node and its index
        on the stack, but is 30% faster in the average case and 3x faster in
        the worst case (for a comb tree).

        Zongzhi Liu's slower but more compact version is:

        def postorder_zongzhi(self):
            stack = [[self, 0]]
            while stack:
                curr, child_idx = stack[-1]
                if child_idx < len(curr.Children):
                    stack[-1][1] += 1
                    stack.append([curr.Children[child_idx], 0])
                else:
                    yield stack.pop()[0]
        """
        child_index_stack = [0]
        curr = self
        curr_children = self.Children
        curr_children_len = len(curr_children)
        while 1:
            curr_index = child_index_stack[-1]
            #if there are children left, process them
            if curr_index < curr_children_len:
                curr_child = curr_children[curr_index]
                #if the current child has children, go there
                if curr_child.Children:
                    child_index_stack.append(0)
                    curr = curr_child
                    curr_children = curr.Children
                    curr_children_len = len(curr_children)
                    curr_index = 0
                #otherwise, yield that child
                else:
                    yield curr_child
                    child_index_stack[-1] += 1
            #if there are no children left, return self, and move to
            #self's parent
            else:
                if include_self or (curr is not self):
                    yield curr
                if curr is self:
                    break
                curr = curr.Parent
                curr_children = curr.Children
                curr_children_len = len(curr_children)
                child_index_stack.pop()
                child_index_stack[-1] += 1

    def pre_and_postorder(self, include_self=True):
        """Performs iteration over tree, visiting node before and after."""
        #handle simple case first
        if not self.Children:
            if include_self:
                yield self
            raise StopIteration
        child_index_stack = [0]
        curr = self
        curr_children = self.Children
        while 1:
            curr_index = child_index_stack[-1]
            if not curr_index:
                if include_self or (curr is not self):
                    yield curr
            #if there are children left, process them
            if curr_index < len(curr_children):
                curr_child = curr_children[curr_index]
                #if the current child has children, go there
                if curr_child.Children:
                    child_index_stack.append(0)
                    curr = curr_child
                    curr_children = curr.Children
                    curr_index = 0
                #otherwise, yield that child
                else:
                    yield curr_child
                    child_index_stack[-1] += 1
            #if there are no children left, return self, and move to
            #self's parent
            else:
                if include_self or (curr is not self):
                    yield curr
                if curr is self:
                    break
                curr = curr.Parent
                curr_children = curr.Children
                child_index_stack.pop()
                child_index_stack[-1] += 1

    def levelorder(self, include_self=True):
        """Performs levelorder iteration over tree"""
        queue = [self]
        while queue:
            curr = queue.pop(0)
            if include_self or (curr is not self):
                yield curr
            if curr.Children:
                queue.extend(curr.Children)

    def tips(self, include_self=False):
        """Iterates over tips descended from self, [] if self is a tip."""
        #bail out in easy case
        if not self.Children:
            if include_self:
                yield self
            raise StopIteration

        stack = [self]
        while stack:
            curr = stack.pop()
            if curr.Children:
                stack.extend(curr.Children[::-1])   #20% faster than reversed
            else:
                yield curr

    def non_tips(self, include_self=False):
        """Iterates over nontips descended from self, [] if none.

        include_self, if True (default is False), will return the current
        node as part of the list of nontips if it is a nontip."""
        for n in self.traverse(True, False, include_self):
            if n.Children:
                yield n

    ### end traversal methods ###

    ### search methods ###

    def invalidate_node_cache(self):
        """Delete the node cache"""
        self._node_cache = {}

    def create_node_cache(self):
        """Construct an internal lookup keyed by node name, valued by node

        This method will not cache nodes in which the .Name is None. This
        method will raise DuplicateNodeError if a name conflict is discovered.
        """
        if self._node_cache:
            return

        for node in self.traverse():
            name = node.Name
            if name is None:
                continue

            if name in self._node_cache:
                raise DuplicateNodeError("%s already exists!" % name)

            self._node_cache[name] = node

    def find(self, name):
        """Find a node by name

        This method returns raises MissingNodeError if the node is not found.
        The first time this method is called, an internal cache is
        constructed to improve performance on subsequent calls.
        """
        # if what is being passed in looks like a node, just return it
        if isinstance(name, self.__class__):
            return name

        self.create_node_cache()
        node = self._node_cache.get(name, None)

        if node is None:
            raise MissingNodeError("Node %s is not in self" % name)
        else:
            return node

    ### path methods ###
    def ancestors(self):
        """Returns all ancestors back to the root. Dynamically calculated."""
        if self.is_root():
            return []

        result = []
        curr = self.Parent
        while not curr.is_root():
            result.append(curr)
            curr = curr.Parent
        result.append(curr)

        return result

    def root(self):
        """Returns root of the tree self is in. Dynamically calculated."""
        if self.is_root():
            return self

        curr = self
        while not curr.is_root():
            curr = curr.Parent
        return curr

    def siblings(self):
        """Returns all nodes that are children of the same parent as self.

        Note: excludes self from the list. Dynamically calculated.
        """
        if self.is_root():
            return []

        result = self.Parent.Children[:]
        result.remove(self)

        return result

    def lowest_common_ancestor(self, tipnames):
        """Lowest common ancestor for a list of tipnames

        This should be around O(H sqrt(n)), where H is height and n is the
        number of tips passed in.
        """
        if len(tipnames) == 1:
            return self.find(tipnames[0])

        tips = [self.find(name) for name in tipnames]

        if len(tips) == 0:
            return None

        nodes_to_scrub = []

        for t in tips:
            prev = t
            curr = t.Parent

            while curr and not hasattr(curr, 'black'):
                setattr(curr, 'black', [prev])
                nodes_to_scrub.append(curr)
                prev = curr
                curr = curr.Parent

            # increase black count, multiple children lead to here
            if curr:
                curr.black.append(prev)

        curr = self
        while len(curr.black) == 1:
            curr = curr.black[0]

        # clean up tree
        for n in nodes_to_scrub:
            delattr(n, 'black')

        return curr

    lca = lowest_common_ancestor #for convenience

    ### end path methods ###

    ### formatters ###
    def to_newick(self, with_distances=False, semicolon=True, escape_name=True):
        """Return the newick string for this tree.

        Arguments:
            - with_distances: whether branch lengths are included.
            - semicolon: end tree string with a semicolon
            - escape_name: if any of these characters []'"(),:;_ exist in a
                nodes name, wrap the name in single quotes

        NOTE: This method returns the Newick representation of this node
        and its descendents. This method is a modification of an implementation
        by Zongzhi Liu
        """
        result = ['(']
        nodes_stack = [[self, len(self.Children)]]
        node_count = 1

        while nodes_stack:
            node_count += 1
            #check the top node, any children left unvisited?
            top = nodes_stack[-1]
            top_node, num_unvisited_children = top
            if num_unvisited_children: #has any child unvisited
                top[1] -= 1  #decrease the #of children unvisited
                next_child = top_node.Children[-num_unvisited_children] # - for order
                #pre-visit
                if next_child.Children:
                    result.append('(')
                nodes_stack.append([next_child, len(next_child.Children)])
            else:  #no unvisited children
                nodes_stack.pop()
                #post-visit
                if top_node.Children:
                    result[-1] = ')'

                if top_node.Name is None:
                    name = ''
                else:
                    name = str(top_node.Name)
                    if escape_name and not (name.startswith("'") and \
                                            name.endswith("'")):
                        if re.search("""[]['"(),:;_]""", name):
                            name = "'%s'" % name.replace("'", "''")
                        else:
                            name = name.replace(' ','_')
                result.append(name)

                if with_distances and top_node.Length is not None:
                    result[-1] = "%s:%s" % (result[-1], top_node.Length)

                result.append(',')

        len_result = len(result)
        if len_result == 2:  # single node no name
            if semicolon:
                return ";"
            else:
                return ''
        elif len_result == 3: # single node with name
            if semicolon:
                return "%s;" % result[1]
            else:
                return result[1]
        else:
            if semicolon:
                result[-1] = ';'
            else:
                result.pop(-1)
            return ''.join(result)

    def _ascii_art(self, char1='-', show_internal=True, compact=False):
        LEN = 10
        PAD = ' ' * LEN
        PA = ' ' * (LEN-1)
        namestr = self.Name or '' # prevents name of NoneType
        if self.Children:
            mids = []
            result = []
            for c in self.Children:
                if c is self.Children[0]:
                    char2 = '/'
                elif c is self.Children[-1]:
                    char2 = '\\'
                else:
                    char2 = '-'
                (clines, mid) = c._ascii_art(char2, show_internal, compact)
                mids.append(mid+len(result))
                result.extend(clines)
                if not compact:
                    result.append('')
            if not compact:
                result.pop()
            (lo, hi, end) = (mids[0], mids[-1], len(result))
            prefixes = [PAD] * (lo+1) + [PA+'|'] * (hi-lo-1) + [PAD] * (end-hi)
            mid = (lo + hi) / 2
            prefixes[mid] = char1 + '-'*(LEN-2) + prefixes[mid][-1]
            result = [p+l for (p,l) in zip(prefixes, result)]
            if show_internal:
                stem = result[mid]
                result[mid] = stem[0] + namestr + stem[len(namestr)+1:]
            return (result, mid)
        else:
            return ([char1 + '-' + namestr], 0)

    def ascii_art(self, show_internal=True, compact=False):
        """Returns a string containing an ascii drawing of the tree.

        Arguments:
        - show_internal: includes internal edge names.
        - compact: use exactly one line per tip.

        Note, this method calls a private recursive function and is not safe
        for large trees.
        """
        (lines, mid) = self._ascii_art(
                show_internal=show_internal, compact=compact)
        return '\n'.join(lines)

    ### end formatters ###

    ### distance methods ###
    def _accumulate_to_ancestor(self, ancestor):
        """Return the sum of the distance between self and ancestor"""
        accum = 0.0
        curr = self
        while curr is not ancestor:
            if curr.is_root():
                raise NoParentError("Provided ancestor is not in the path")

            if curr.Length is None:
                raise NoLengthError

            accum += curr.Length
            curr = curr.Parent

        return accum

    def distance(self, other):
        """Return the distance between self and other"""
        if self is other:
            return 0.0

        root = self.root()
        lca = root.lowest_common_ancestor([self, other])
        accum = self._accumulate_to_ancestor(lca)
        accum += other._accumulate_to_ancestor(lca)

        return accum

    ### make max distance a property?
    def _set_max_distance(self):
        """Propagate tip distance information up the tree

        This method was originally implemented by Julia Goodrich with the intent
        of being able to determine max tip to tip distances between nodes on
        large trees efficiently. The code has been modified to track the
        specific tips the distance is between
        """
        for n in self.postorder():
            if n.is_tip():
                n.MaxDistTips = [[0.0, n], [0.0, n]]
            else:
                if len(n.Children) == 1:
                    tip_a, tip_b = n.Children[0].MaxDistTips
                    tip_a[0] += n.Children[0].Length or 0.0
                    tip_b[0] += n.Children[0].Length or 0.0
                else:
                    tip_info = [(max(c.MaxDistTips), c) for c in n.Children]
                    dists = [i[0][0] for i in tip_info]
                    best_idx = argsort(dists)[-2:]
                    tip_a, child_a = tip_info[best_idx[0]]
                    tip_b, child_b = tip_info[best_idx[1]]
                    tip_a[0] += child_a.Length or 0.0
                    tip_b[0] += child_b.Length or 0.0
                n.MaxDistTips = [tip_a, tip_b]

    def get_max_distance(self):
        """Returns the max tip tip distance between any pair of tips

        Returns (dist, tips)
        """
        if not hasattr(self, 'MaxDistTips'):
            self._set_max_distance()

        longest = 0.0
        tips = [None, None]
        for n in self.non_tips(include_self=True):
            tip_a, tip_b = n.MaxDistTips
            dist = (tip_a[0] + tip_b[0])

            if dist > longest:
                longest = dist
                tips = [tip_a[1], tip_b[1]]
        return longest, tips

    def tip_tip_distances(self, endpoints=None, default_length=1):
        """Returns distance matrix between all pairs of tips, and a tip order.

        Warning: .__start and .__stop added to self and its descendants.

        tip_order contains the actual node objects, not their names (may be
        confusing in some cases).
        """
        all_tips = list(self.tips())
        if endpoints is None:
            tip_order = all_tips
        else:
            tip_order = [self.find(n) for n in endpoints]

        ## linearize all tips in postorder
        # .__start, .__stop compose the slice in tip_order.
        for i, node in enumerate(all_tips):
            node.__start, node.__stop = i, i+1

        # the result map provides index in the result matrix
        result_map = {n.__start: i for i, n in enumerate(tip_order)}
        num_all_tips = len(all_tips)  # total number of tips
        num_tips = len(tip_order)  # total number of tips in result
        result = zeros((num_tips, num_tips), float)  # tip by tip matrix
        distances = zeros((num_all_tips), float)  # dist from tip to curr node

        def update_result():
        # set tip_tip distance between tips of different child
            for child1, child2 in combinations(node.Children, 2):
                for tip1 in range(child1.__start, child1.__stop):
                    if tip1 not in result_map:
                        continue
                    t1idx = result_map[tip1]
                    for tip2 in range(child2.__start, child2.__stop):
                        if tip2 not in result_map:
                            continue
                        t2idx = result_map[tip2]
                        result[t1idx, t2idx] = distances[tip1]+distances[tip2]

        for node in self.postorder():
            if not node.Children:
                continue
            ## subtree with solved child wedges
            ### can possibly use np.zeros
            starts, stops = [], []  # to calc ._start and ._stop for curr node
            for child in node.Children:
                if child.Length is not None:
                    child_len = child.Length
                else:
                    child_len = default_length

                distances[child.__start:child.__stop] += child_len

                starts.append(child.__start)
                stops.append(child.__stop)

            node.__start, node.__stop = min(starts), max(stops)

            if len(node.Children) > 1:
                update_result()

        return result + result.T, tip_order

    ### end distance methods ###

    ### comparison methods ###

    def compare_subsets(self, other):
        raise NotImplementedError()

    def compare_tip_distances(self, other, sample=None, dist_f=distance_from_r,
            shuffle_f=shuffle):
        """Compares self to other using tip-to-tip distance matrices.

        Value returned is dist_f(m1, m2) for the two matrices. Default is
        to use the Pearson correlation coefficient, with +1 giving a distance
        of 0 and -1 giving a distance of +1 (the madimum possible value).
        Depending on the application, you might instead want to use
        distance_from_r_squared, which counts correlations of both +1 and -1
        as identical (0 distance).

        Note: automatically strips out the names that don't match (this is
        necessary for this method because the distance between non-matching
        names and matching names is undefined in the tree where they don't
        match, and because we need to reorder the names in the two trees to
        match up the distance matrices).
        """
        self_names = {i.Name: i for i in self.tips()}
        other_names = {i.Name: i for i in other.tips()}
        common_names = frozenset(self_names) & frozenset(other_names)
        common_names = list(common_names)

        if not common_names:
            raise ValueError("No names in common between the two trees.")

        if len(common_names) <= 2:
            return 1  # the two trees must match by definition in this case

        if sample is not None:
            shuffle_f(common_names)
            common_names = common_names[:sample]

        self_nodes = [self_names[k] for k in common_names]
        other_nodes = [other_names[k] for k in common_names]

        self_matrix = self.tip_tip_distances(endpoints=self_nodes)[0]
        other_matrix = other.tip_tip_distances(endpoints=other_nodes)[0]

        return dist_f(self_matrix, other_matrix)

    ### end comparison methods ###
