"""
Base memory provider interface
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from .memory_types import MemoryRequest, MemoryResponse, TrajectoryData, MemoryType, MemoryStatus


class BaseMemoryProvider(ABC):
    """Abstract base class for memory providers"""

    def __init__(self, memory_type: MemoryType, config: Optional[dict] = None):
        self.memory_type = memory_type
        self.config = config or {}

    @abstractmethod
    def provide_memory(self, request: MemoryRequest) -> MemoryResponse:
        """
        Retrieve relevant memories based on query, context and status
        
        Args:
            request: MemoryRequest containing query, context, status and optional params
            
        Returns:
            MemoryResponse containing relevant memories
        """
        pass

    @abstractmethod
    def take_in_memory(self, trajectory_data: TrajectoryData) -> tuple[bool, str]:
        """
        Store/ingest new memory from trajectory data

        Args:
            trajectory_data: TrajectoryData containing query, trajectory and metadata

        Returns:
            tuple[bool, str]: (Success status of memory ingestion, Description of absorbed memory)
        """
        pass

    @abstractmethod
    def initialize(self) -> bool:
        """
        Initialize the memory provider (load existing data, setup indices, etc.)
        
        Returns:
            bool: Success status of initialization
        """
        pass

    def get_memory_type(self) -> MemoryType:
        """Get the type of this memory provider"""
        return self.memory_type

    def get_config(self) -> dict:
        """Get the configuration of this memory provider"""
        return self.config.copy()

    def probe(self, query: str, top_k: int = 1) -> "ProbeResult":
        from .memory_agent.decision_types import ProbeResult, ProbeItem
        try:
            request = MemoryRequest(
                query=query,
                context="",
                status=MemoryStatus.BEGIN,
            )
            response = self.provide_memory(request)
            if response and response.memories:
                items = [
                    ProbeItem(
                        snippet=str(m.content)[:200],
                        score=getattr(m, 'score', None),
                        metadata_summary="",
                    )
                    for m in response.memories[:top_k]
                ]
                return ProbeResult(
                    provider_name=self.memory_type.value,
                    items=items,
                    total_stored=getattr(response, 'total_count', 0),
                )
        except Exception:
            pass
        return ProbeResult(provider_name=self.memory_type.value, items=[])

    def query_task_memory(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Query task-level memory (shortterm_memory) for facts within current task
        
        Args:
            query: Search query for relevant facts
            top_k: Maximum number of results to return
            
        Returns:
            List of relevant fact items with metadata
            
        Note: Default implementation returns empty list.
              Subclasses should override this method.
        """
        return []

    def store_task_memory(self, fact: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Store a fact into task-level memory (shortterm_memory)
        
        Args:
            fact: The fact/data to store (e.g., "Beijing population: 21.5 million")
            metadata: Optional metadata (source, confidence, etc.)
            
        Returns:
            bool: Success status
            
        Note: Default implementation returns False.
              Subclasses should override this method.
        """
        return False

    def get_all_task_memory(self) -> List[str]:
        """
        Get all items in current task memory
        
        Returns:
            List of all task memory items
            
        Note: Default implementation returns empty list.
              Subclasses should override this method.
        """
        return []

    def clear_task_memory(self) -> bool:
        """
        Clear all task-level memory (typically called at task end)
        
        Returns:
            bool: Success status
            
        Note: Default implementation returns False.
              Subclasses should override this method.
        """
        return False

    def query_experience_memory(
        self,
        query: str,
        experience_type: Optional[str] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Query cross-task experience memory (longterm_db)
        
        Args:
            query: Search query for relevant experience
            experience_type: Optional filter - "strategic", "operational", or None for both
            top_k: Maximum number of results to return
            
        Returns:
            List of relevant experience items with metadata
            
        Note: Default implementation returns empty list.
              Subclasses should override this method.
        """
        return []

    def store_experience_memory(
        self,
        experience: str,
        experience_type: str = "operational",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Store an experience into cross-task memory (longterm_db)
        
        Args:
            experience: The experience/lesson learned to store
            experience_type: "strategic" (high-level principles) or "operational" (specific procedures)
            tags: Optional tags for categorization
            metadata: Optional additional metadata
            
        Returns:
            bool: Success status
            
        Note: Default implementation returns False.
              Subclasses should override this method.
        """
        return False

    def get_experience_stats(self) -> Dict[str, Any]:
        """
        Get statistics about experience memory
        
        Returns:
            Dict with stats like total count, breakdown by type, etc.
            
        Note: Default implementation returns empty dict.
              Subclasses should override this method.
        """
        return {}
