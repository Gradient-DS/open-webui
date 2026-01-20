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
	let selectedDocuments: Set<string> = new Set(); // Selected document titles
	
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
	 * Select all documents across all collections
	 */
	const selectAllDocumentsDefault = () => {
		collections.forEach(collection => {
			collection.documents.forEach(doc => {
				selectedDocuments.add(doc.title);
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
	const toggleDocument = (title: string) => {
		if (selectedDocuments.has(title)) {
			selectedDocuments.delete(title);
		} else {
			selectedDocuments.add(title);
		}
		selectedDocuments = selectedDocuments; // Trigger reactivity
		emitFilterChange();
	};
	
	/**
	 * Select all documents in a collection
	 */
	const selectAllDocuments = (collection: RagCollection) => {
		collection.documents.forEach(doc => {
			selectedDocuments.add(doc.title);
		});
		selectedDocuments = selectedDocuments;
		emitFilterChange();
	};
	
	/**
	 * Deselect all documents in a collection
	 */
	const deselectAllDocuments = (collection: RagCollection) => {
		collection.documents.forEach(doc => {
			selectedDocuments.delete(doc.title);
		});
		selectedDocuments = selectedDocuments;
		emitFilterChange();
	};
	
	/**
	 * Select all documents in a subtype
	 */
	const selectAllDocumentsInSubtype = (documents: RagDocument[]) => {
		documents.forEach(doc => {
			selectedDocuments.add(doc.title);
		});
		selectedDocuments = selectedDocuments;
		emitFilterChange();
	};
	
	/**
	 * Deselect all documents in a subtype
	 */
	const deselectAllDocumentsInSubtype = (documents: RagDocument[]) => {
		documents.forEach(doc => {
			selectedDocuments.delete(doc.title);
		});
		selectedDocuments = selectedDocuments;
		emitFilterChange();
	};
	
	/**
	 * Clear all filters
	 */
	const clearFilters = () => {
		selectedDocuments = new Set();
		expandedCollections = new Set();
		expandedSubtypes = new Set();
		emitFilterChange();
	};
	
	/**
	 * Emit filter change event to parent component
	 */
	const emitFilterChange = () => {
		// Get collections that have selected documents
		const collectionsWithSelectedDocs = new Set<string>();
		collections.forEach(collection => {
			const hasSelectedDoc = collection.documents.some(doc => 
				selectedDocuments.has(doc.title)
			);
			if (hasSelectedDoc) {
				collectionsWithSelectedDocs.add(collection.collection_key);
			}
		});
		
		// If no documents selected, include all collections
		const activeCollections = selectedDocuments.size > 0
			? Array.from(collectionsWithSelectedDocs)
			: collections.map(c => c.collection_key);
		
		dispatch('filterChange', {
			mode: selectedDocuments.size > 0 ? 'documents' : 'collections',
			collections: activeCollections,
			documents: Array.from(selectedDocuments)
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
						<!-- Collection Header (Accordion Toggle) -->
						<button
							on:click={() => toggleExpanded(collection.collection_key)}
							class="w-full flex items-center justify-between px-3 py-2.5 bg-gray-50 dark:bg-gray-800/50 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
						>
							<div class="flex-1 text-left">
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
						
						<!-- Document Subtypes List (Collapsible) -->
						{#if expandedCollections.has(collection.collection_key)}
							<div class="bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700">
								{#if collection.documents.length > 0}
									{@const groupedDocs = groupDocumentsBySubtype(collection.documents)}
									{@const subtypes = Array.from(groupedDocs.entries())}
									
									<!-- Collection-level Bulk Actions -->
									<div class="px-3 py-2 border-b border-gray-100 dark:border-gray-800">
										<div class="flex gap-2">
											<button
												on:click={() => selectAllDocuments(collection)}
												class="text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400"
											>
												Select All
											</button>
											<span class="text-gray-300 dark:text-gray-600">|</span>
											<button
												on:click={() => deselectAllDocuments(collection)}
												class="text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400"
											>
												Deselect All
											</button>
										</div>
									</div>
									
									<!-- Document Subtypes -->
									<div class="space-y-1">
										{#each subtypes as [subtype, documents]}
											{@const subtypeKey = getSubtypeKey(collection.collection_key, subtype)}
											<div class="border-b border-gray-100 dark:border-gray-800 last:border-b-0">
												<!-- Subtype Header (Accordion Toggle) -->
												<button
													on:click={() => toggleSubtypeExpanded(collection.collection_key, subtype)}
													class="w-full flex items-center justify-between px-4 py-2 bg-gray-50/50 dark:bg-gray-800/30 hover:bg-gray-100 dark:hover:bg-gray-800/50 transition"
												>
													<div class="flex-1 text-left">
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
																<div class="flex items-start gap-2 py-1.5 px-1 rounded hover:bg-gray-50 dark:hover:bg-gray-800/50">
																	<Checkbox
																		state={selectedDocuments.has(doc.title) ? 'checked' : 'unchecked'}
																		on:change={() => toggleDocument(doc.title)}
																	/>
																	<button
																		on:click={() => toggleDocument(doc.title)}
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
