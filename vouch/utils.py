from collections import deque
from django.contrib.auth import get_user_model
from .models import Connection

User = get_user_model()

def get_network_graph():
    """Builds an adjacency list of the entire connection graph (single DB hit)."""
    connections = Connection.objects.values_list('user1_id', 'user2_id')
    graph = {}
    for u1, u2 in connections:
        graph.setdefault(u1, []).append(u2)
    return graph

def find_shortest_path(start_user_id, target_user_id, max_depth=6):
    """
    BFS to find ONE shortest path between two users.
    Returns a list of User objects, or None.
    """
    if start_user_id == target_user_id:
        return [User.objects.get(id=start_user_id)]
    
    graph = get_network_graph()
    if start_user_id not in graph or target_user_id not in graph:
        return None
    
    queue = deque([[start_user_id]])
    visited = {start_user_id}
    
    while queue:
        path = queue.popleft()
        if len(path) > max_depth + 1:
            continue
        
        current = path[-1]
        if current == target_user_id:
            users = User.objects.in_bulk(path)
            return [users[uid] for uid in path]
        
        for neighbor in graph.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(path + [neighbor])
    
    return None

def find_all_shortest_paths(start_user_id, target_user_id, max_depth=6):
    """
    BFS to find ALL shortest paths between two users.
    Returns a list of paths (each path is a list of User objects), or empty list.
    """
    if start_user_id == target_user_id:
        return [[User.objects.get(id=start_user_id)]]
    
    graph = get_network_graph()
    if start_user_id not in graph or target_user_id not in graph:
        return []
    
    queue = deque([[start_user_id]])
    # Track distance at which each node was first visited
    visited_at_depth = {start_user_id: 0}
    all_paths = []
    shortest_length = None
    
    while queue:
        path = queue.popleft()
        current_depth = len(path) - 1
        
        # If we already found paths and this path is longer, stop
        if shortest_length is not None and len(path) > shortest_length:
            break
        
        if current_depth > max_depth:
            continue
        
        current = path[-1]
        
        if current == target_user_id:
            if shortest_length is None:
                shortest_length = len(path)
            all_paths.append(path)
            continue
        
        for neighbor in graph.get(current, []):
            next_depth = current_depth + 1
            # Allow revisiting a node if it's at the same depth (to find all paths)
            if neighbor not in visited_at_depth or visited_at_depth[neighbor] >= next_depth:
                visited_at_depth[neighbor] = next_depth
                queue.append(path + [neighbor])
    
    if not all_paths:
        return []
    
    # Resolve user IDs to User objects
    all_ids = set()
    for p in all_paths:
        all_ids.update(p)
    users = User.objects.in_bulk(list(all_ids))
    
    return [[users[uid] for uid in path] for path in all_paths]
