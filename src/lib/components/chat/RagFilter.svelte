<script lang="ts">
	import { onMount, createEventDispatcher } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getCollectionsAndDocuments, type RagCollection, type RagDocument } from '$lib/apis/rag';
	
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	
	const dispatch = createEventDispatcher();
	
	// State
	let loading = true;
	let collections: RagCollection[] = [];
	let expandedCollections: Set<string> = new Set(); // Which collections are expanded
	let expandedSubtypes: Set<string> = new Set(); // Which document subtypes are expanded (format: "collectionKey:subtype")
	let selectedDocuments: Set<string> = new Set(); // Selected document IDs
	
	// Database info
	let databaseName = '';
	
	/**
	 * Group documents by subtype within a collection
	 */
	const groupDocumentsBySubtype = (documents: RagDocument[]): Map<string, RagDocument[]> => {
		const grouped = new Map<string, RagDocument[]>();
		
		documents.forEach(doc => {
			const subtype = doc.contentsubtype || 'Niet gecategoriseerd';
			if (!grouped.has(subtype)) {
				grouped.set(subtype, []);
			}
			grouped.get(subtype)!.push(doc);
		});
		
		return grouped;
	};
	
	/**
	 * Get unique key for a subtype within a collection
	 */
	const getSubtypeKey = (collectionKey: string, subtype: string): string => {
		return `${collectionKey}:${subtype}`;
	};
	
	/**
	 * Get document ID for Set matching
	 * Returns empty string if no ID (documents without IDs are excluded from filtering)
	 */
	const getDocumentId = (doc: RagDocument): string => {
		if (doc.id !== undefined && doc.id !== null && doc.id !== '') {
			return String(doc.id); // Convert to string for Set matching
		}
		return ''; // No fallback - documents without IDs are excluded
	};
	
	/**
	 * Select all documents across all collections (only documents with IDs)
	 */
	const selectAllDocumentsDefault = () => {
		collections.forEach(collection => {
			collection.documents.forEach(doc => {
				const docId = getDocumentId(doc);
				// Only add documents that have valid IDs
				if (docId) {
					selectedDocuments.add(docId);
				}
			});
		});
		selectedDocuments = selectedDocuments; // Trigger reactivity
	};
	
	/**
	 * Load collections and documents from the API
	 */
	const loadCollectionsAndDocuments = async () => {
		loading = true;
		
		try {
			const data = await getCollectionsAndDocuments();
			
			if (data) {
				collections = data.collections;
				databaseName = data.database.display_name;
				
				// Select all documents by default
				selectAllDocumentsDefault();
				emitFilterChange();
			} else {
				toast.error('Failed to load RAG filter data');
			}
		} catch (err) {
			console.error('[RAG Filter]', err);
			toast.error('Error loading RAG filters');
		} finally {
			loading = false;
		}
	};
	
	/**
	 * Toggle collection expansion (show/hide subtypes)
	 */
	const toggleExpanded = (collectionKey: string) => {
		if (expandedCollections.has(collectionKey)) {
			expandedCollections.delete(collectionKey);
		} else {
			expandedCollections.add(collectionKey);
		}
		expandedCollections = expandedCollections; // Trigger reactivity
	};
	
	/**
	 * Toggle document subtype expansion (show/hide documents)
	 */
	const toggleSubtypeExpanded = (collectionKey: string, subtype: string) => {
		const key = getSubtypeKey(collectionKey, subtype);
		if (expandedSubtypes.has(key)) {
			expandedSubtypes.delete(key);
		} else {
			expandedSubtypes.add(key);
		}
		expandedSubtypes = expandedSubtypes; // Trigger reactivity
	};
	
	/**
	 * Toggle document selection
	 */
	const toggleDocument = (docId: string) => {
		if (selectedDocuments.has(docId)) {
			selectedDocuments.delete(docId);
		} else {
			selectedDocuments.add(docId);
		}
		selectedDocuments = selectedDocuments; // Trigger reactivity
		emitFilterChange();
	};
	
	/**
	 * Get checkbox state for a collection (checked/unchecked/indeterminate)
	 * Only counts documents with valid IDs
	 */
	const getCollectionCheckboxState = (collection: RagCollection): 'checked' | 'unchecked' => {
		// Filter to only documents with IDs
		const documentsWithIds = collection.documents.filter(doc => {
			const docId = getDocumentId(doc);
			return docId !== '';
		});
		
		if (documentsWithIds.length === 0) return 'unchecked';
		
		const selectedCount = documentsWithIds.filter(doc => 
			selectedDocuments.has(getDocumentId(doc))
		).length;
		
		if (selectedCount === 0) return 'unchecked';
		if (selectedCount === documentsWithIds.length) return 'checked';
		return 'unchecked'; // Partial selection - we'll use indeterminate prop
	};
	
	/**
	 * Check if collection has partial selection (some but not all selected)
	 * Only counts documents with valid IDs
	 */
	const isCollectionIndeterminate = (collection: RagCollection): boolean => {
		// Filter to only documents with IDs
		const documentsWithIds = collection.documents.filter(doc => {
			const docId = getDocumentId(doc);
			return docId !== '';
		});
		
		if (documentsWithIds.length === 0) return false;
		
		const selectedCount = documentsWithIds.filter(doc => 
			selectedDocuments.has(getDocumentId(doc))
		).length;
		
		return selectedCount > 0 && selectedCount < documentsWithIds.length;
	};
	
	/**
	 * Toggle collection selection (select all or deselect all)
	 * Only operates on documents with valid IDs
	 */
	const toggleCollection = (collection: RagCollection, event?: Event) => {
		if (event) {
			event.stopPropagation(); // Prevent expanding/collapsing
		}
		
		// Filter to only documents with IDs
		const documentsWithIds = collection.documents.filter(doc => {
			const docId = getDocumentId(doc);
			return docId !== '';
		});
		
		const allSelected = documentsWithIds.length > 0 && documentsWithIds.every(doc => 
			selectedDocuments.has(getDocumentId(doc))
		);
		
		if (allSelected) {
			// Deselect all
			collection.documents.forEach(doc => {
				const docId = getDocumentId(doc);
				if (docId) {
					selectedDocuments.delete(docId);
				}
			});
		} else {
			// Select all (only documents with IDs)
			collection.documents.forEach(doc => {
				const docId = getDocumentId(doc);
				if (docId) {
					selectedDocuments.add(docId);
				}
			});
		}
		
		selectedDocuments = selectedDocuments; // Trigger reactivity
		emitFilterChange();
	};
	
	/**
	 * Get checkbox state for a subtype (checked/unchecked)
	 * Only counts documents with valid IDs
	 */
	const getSubtypeCheckboxState = (documents: RagDocument[]): 'checked' | 'unchecked' => {
		// Filter to only documents with IDs
		const documentsWithIds = documents.filter(doc => {
			const docId = getDocumentId(doc);
			return docId !== '';
		});
		
		if (documentsWithIds.length === 0) return 'unchecked';
		
		const selectedCount = documentsWithIds.filter(doc => 
			selectedDocuments.has(getDocumentId(doc))
		).length;
		
		if (selectedCount === 0) return 'unchecked';
		if (selectedCount === documentsWithIds.length) return 'checked';
		return 'unchecked'; // Partial selection - we'll use indeterminate prop
	};
	
	/**
	 * Check if subtype has partial selection (some but not all selected)
	 * Only counts documents with valid IDs
	 */
	const isSubtypeIndeterminate = (documents: RagDocument[]): boolean => {
		// Filter to only documents with IDs
		const documentsWithIds = documents.filter(doc => {
			const docId = getDocumentId(doc);
			return docId !== '';
		});
		
		if (documentsWithIds.length === 0) return false;
		
		const selectedCount = documentsWithIds.filter(doc => 
			selectedDocuments.has(getDocumentId(doc))
		).length;
		
		return selectedCount > 0 && selectedCount < documentsWithIds.length;
	};
	
	/**
	 * Toggle subtype selection (select all or deselect all)
	 */
	const toggleSubtype = (documents: RagDocument[], event?: Event) => {
		if (event) {
			event.stopPropagation(); // Prevent expanding/collapsing
		}
		
		const allSelected = documents.every(doc => 
			selectedDocuments.has(getDocumentId(doc))
		);
		
		if (allSelected) {
			// Deselect all
			documents.forEach(doc => {
				const docId = getDocumentId(doc);
				if (docId) {
					selectedDocuments.delete(docId);
				}
			});
		} else {
			// Select all (only documents with IDs)
			documents.forEach(doc => {
				const docId = getDocumentId(doc);
				if (docId) {
					selectedDocuments.add(docId);
				}
			});
		}
		
		selectedDocuments = selectedDocuments; // Trigger reactivity
		emitFilterChange();
	};
	
	/**
	 * Select all documents in a subtype (only documents with IDs)
	 */
	const selectAllDocumentsInSubtype = (documents: RagDocument[]) => {
		documents.forEach(doc => {
			const docId = getDocumentId(doc);
			if (docId) {
				selectedDocuments.add(docId);
			}
		});
		selectedDocuments = selectedDocuments;
		emitFilterChange();
	};
	
	/**
	 * Deselect all documents in a subtype
	 */
	const deselectAllDocumentsInSubtype = (documents: RagDocument[]) => {
		documents.forEach(doc => {
			const docId = getDocumentId(doc);
			if (docId) {
				selectedDocuments.delete(docId);
			}
		});
		selectedDocuments = selectedDocuments;
		emitFilterChange();
	};
	
	/**
	 * Clear all filters (select all documents with IDs)
	 */
	const clearFilters = () => {
		selectedDocuments = new Set();
		// Select all documents (only those with IDs)
		collections.forEach(collection => {
			collection.documents.forEach(doc => {
				const docId = getDocumentId(doc);
				if (docId) {
					selectedDocuments.add(docId);
				}
			});
		});
		selectedDocuments = selectedDocuments; // Trigger reactivity
		expandedCollections = new Set();
		expandedSubtypes = new Set();
		emitFilterChange();
	};
	
	/**
	 * Emit filter change event to parent component
	 */
	const emitFilterChange = () => {
		const collectionsFilter: Record<string, { doc_ids: (string | number)[]; doc_titles: string[] }> = {};
		
		if (selectedDocuments.size > 0) {
			// Collect all selected documents grouped by collection
			for (const collection of collections) {
				const collectionDocIds: (string | number)[] = [];
				const collectionDocTitles: string[] = [];
				
				for (const doc of collection.documents) {
					// Skip documents without IDs - they cannot be filtered
					if (doc.id === undefined || doc.id === null || doc.id === '') {
						continue;
					}
					
					const docId = getDocumentId(doc); // Used for matching against selectedDocuments Set
					if (selectedDocuments.has(docId)) {
						// Preserve the original type - if it's a number, keep it as number
						const stableId: string | number = typeof doc.id === 'number' 
							? doc.id 
							: (typeof doc.id === 'string' ? doc.id.trim() : String(doc.id));
						
						// Only add if ID is valid after processing
						if (typeof stableId === 'string' && stableId.length === 0) {
							continue; // Skip empty string IDs
						}
						
						collectionDocIds.push(stableId);
						collectionDocTitles.push(doc.title || '');
					}
				}
				
				// Only add collection if it has selected documents
				if (collectionDocIds.length > 0) {
					collectionsFilter[collection.collection_key] = {
						doc_ids: collectionDocIds,
						doc_titles: collectionDocTitles
					};
				}
			}
		}
		
		console.log('[RAG Filter] emitFilterChange collectionsFilter', collectionsFilter);
		dispatch('filterChange', {
			collections: collectionsFilter
		});
	};
	
	onMount(() => {
		loadCollectionsAndDocuments();
	});
</script>

<div class="rag-filter-panel flex flex-col h-full">

	{#if databaseName}
		<div class="px-4 pt-3 text-[11px] text-black-400 dark:text-gray-500 uppercase tracking-wide">
		<span class="normal-case font-medium">{databaseName}</span>
		</div>
	{/if}

	<div class="px-4 py-4 dark:border-gray-700">
		<div class="flex items-center justify-between mb-2">
			<button
				on:click={clearFilters}
				class="text-xs px-3 py-1.5 rounded-md
				       border border-gray-300 dark:border-gray-600
				       text-gray-700 dark:text-gray-200
				       hover:bg-gray-100 dark:hover:bg-gray-700
				       transition"
				title="Clear all filters"
			>
				Verwijder alle filters
			</button>
		</div>
	</div>
	
	<!-- Content - Accordion Style -->
	<div class="flex-1 overflow-y-auto px-4 py-1">
		{#if loading}
			<div class="flex justify-center items-center h-32">
				<Spinner className="size-6" />
			</div>
		{:else if collections.length === 0}
			<div class="text-center text-sm text-gray-500 dark:text-gray-400 py-8">
				No collections available
			</div>
		{:else}
			<!-- Accordion Items -->
			<div class="space-y-2">
				{#each collections as collection}
					<div class="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
						<!-- Collection Header -->
						<div class="flex items-center gap-2 px-3 py-2.5 bg-gray-50 dark:bg-gray-800/50">
							<!-- Collection Checkbox -->
							<div
								on:click|stopPropagation
								class="cursor-pointer"
								role="button"
								tabindex="0"
								on:keydown={(e) => {
									if (e.key === 'Enter' || e.key === ' ') {
										e.preventDefault();
										e.stopPropagation();
										toggleCollection(collection, e);
									}
								}}
								aria-label="Toggle collection selection"
							>
								<Checkbox
									state={getCollectionCheckboxState(collection)}
									indeterminate={isCollectionIndeterminate(collection)}
									on:change={() => toggleCollection(collection)}
								/>
							</div>
							
							<!-- Collection Info and Expand Button -->
							<button
								on:click={() => toggleExpanded(collection.collection_key)}
								class="flex-1 flex items-center justify-between text-left"
							>
								<div class="flex-1">
									<div class="text-sm font-medium text-gray-800 dark:text-gray-100">
										{collection.collection_key}
									</div>
									<div class="text-xs text-gray-500 dark:text-gray-400">
										{collection.document_count} document{collection.document_count !== 1 ? 'en' : ''}
									</div>
								</div>
								
								<!-- Expand/Collapse Icon -->
								{#if collection.documents.length > 0}
									<svg
										class="w-5 h-5 text-gray-400 transition-transform {expandedCollections.has(collection.collection_key) ? 'rotate-180' : ''}"
										fill="none"
										stroke="currentColor"
										viewBox="0 0 24 24"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											stroke-width="2"
											d="M19 9l-7 7-7-7"
										/>
									</svg>
								{/if}
							</button>
						</div>
						
						<!-- Document Subtypes List (Collapsible) -->
						{#if expandedCollections.has(collection.collection_key)}
							<div class="bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700">
								{#if collection.documents.length > 0}
									{@const groupedDocs = groupDocumentsBySubtype(collection.documents)}
									{@const subtypes = Array.from(groupedDocs.entries())}
									
									<!-- Document Subtypes -->
									<div class="space-y-1">
										{#each subtypes as [subtype, documents]}
											{@const subtypeKey = getSubtypeKey(collection.collection_key, subtype)}
											<div class="border-b border-gray-100 dark:border-gray-800 last:border-b-0">
												<!-- Subtype Header -->
												<div class="flex items-center gap-2 px-4 py-2 bg-gray-50/50 dark:bg-gray-800/30">
													<!-- Subtype Checkbox -->
													<div
														on:click|stopPropagation
														class="cursor-pointer"
														role="button"
														tabindex="0"
														on:keydown={(e) => {
															if (e.key === 'Enter' || e.key === ' ') {
																e.preventDefault();
																e.stopPropagation();
																toggleSubtype(documents, e);
															}
														}}
														aria-label="Toggle subtype selection"
													>
														<Checkbox
															state={getSubtypeCheckboxState(documents)}
															indeterminate={isSubtypeIndeterminate(documents)}
															on:change={() => toggleSubtype(documents)}
														/>
													</div>
													
													<!-- Subtype Info and Expand Button -->
													<button
														on:click={() => toggleSubtypeExpanded(collection.collection_key, subtype)}
														class="flex-1 flex items-center justify-between text-left"
													>
														<div class="flex-1">
															<div class="text-xs font-medium text-gray-700 dark:text-gray-300">
																{subtype}
															</div>
															<div class="text-xs text-gray-500 dark:text-gray-400">
																{documents.length} document{documents.length !== 1 ? 'en' : ''}
															</div>
														</div>
														
														<!-- Expand/Collapse Icon -->
														<svg
															class="w-4 h-4 text-gray-400 transition-transform {expandedSubtypes.has(subtypeKey) ? 'rotate-180' : ''}"
															fill="none"
															stroke="currentColor"
															viewBox="0 0 24 24"
														>
															<path
																stroke-linecap="round"
																stroke-linejoin="round"
																stroke-width="2"
																d="M19 9l-7 7-7-7"
															/>
														</svg>
													</button>
												</div>
												
												<!-- Documents List (Collapsible) -->
												{#if expandedSubtypes.has(subtypeKey)}
													<div class="px-4 py-2 bg-white dark:bg-gray-900">
														<!-- Subtype-level Bulk Actions -->
														<div class="flex gap-2 mb-2 pb-2 border-b border-gray-100 dark:border-gray-800">
															<button
																on:click={() => selectAllDocumentsInSubtype(documents)}
																class="text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400"
															>
																Select All
															</button>
															<span class="text-gray-300 dark:text-gray-600">|</span>
															<button
																on:click={() => deselectAllDocumentsInSubtype(documents)}
																class="text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400"
															>
																Deselect All
															</button>
														</div>
														
														<!-- Document List -->
														<div class="space-y-1 max-h-64 overflow-y-auto">
															{#each documents as doc}
																{@const docId = getDocumentId(doc)}
																<div class="flex items-start gap-2 py-1.5 px-1 rounded hover:bg-gray-50 dark:hover:bg-gray-800/50">
																	<Checkbox
																		state={selectedDocuments.has(docId) ? 'checked' : 'unchecked'}
																		on:change={() => toggleDocument(docId)}
																	/>
																	<button
																		on:click={() => toggleDocument(docId)}
																		class="flex-1 text-left text-xs text-gray-700 dark:text-gray-300 leading-relaxed"
																		title={doc.title}
																	>
																		{doc.title}
																	</button>
																</div>
															{/each}
														</div>
													</div>
												{/if}
											</div>
										{/each}
									</div>
								{:else}
									<div class="px-3 py-2 text-xs text-gray-500 dark:text-gray-400 text-center">
										Geen documenten gevonden
									</div>
								{/if}
							</div>
						{/if}
						
						<!-- Error Message -->
						{#if collection.error}
							<div class="px-3 py-2 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20">
								Error: {collection.error}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</div>
	
	<!-- Footer with active filter summary -->
	<div class="px-4 py-3 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-600 dark:text-gray-400">
		{#if selectedDocuments.size > 0}
			<div>
				{selectedDocuments.size} document{selectedDocuments.size !== 1 ? 'en' : ''} geselecteerd
			</div>
		{:else}
			<div>
				Geen filter actief - zoeken in alle collecties
			</div>
		{/if}
	</div>
</div>

<style>
	.rag-filter-panel {
		min-width: 280px;
		max-width: 400px;
	}
</style>
