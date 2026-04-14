# Email: Data-integratie Soev — Octobox

**Onderwerp:** Data-integratie Soev — aansluiting jullie documentpipeline

---

Hoi [naam],

Naar aanleiding van ons gesprek over het koppelen van jullie documentpipeline aan ons platform, heb ik uitgezocht hoe we dit het beste kunnen faciliteren. Hieronder een samenvatting.

**Hoe het werkt**

Jullie pipeline levert al schone, geparsede tekst op uit documenten — dat is een mooi startpunt. Wij nemen het vanaf daar over: het opsplitsen in chunks, embeddings genereren en opslaan in onze vector database (Weaviate). Zo is alles direct doorzoekbaar via de AI-chat.

**Twee integratiemodellen**

Ons platform ondersteunt twee manieren om externe databronnen aan te sluiten:

| Model         | Hoe het werkt                                                                                                             | Voorbeeld                                         |
| ------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| **Connected** | Wij verbinden met jullie systeem en synchroniseren periodiek op de achtergrond. Wij initiëren het ophalen van data.       | OneDrive, SharePoint, Google Drive                |
| **Ingest**    | Jullie sturen data naar ons API-endpoint wanneer er nieuwe of gewijzigde documenten zijn. Jullie initiëren het versturen. | Documentpipelines, ETL-processen, DMS-koppelingen |

Voor jullie use case is het **Ingest-model** het meest geschikt: jullie pipeline produceert documenten en pusht ze naar ons endpoint. Geen polling, geen complexe sync-setup — gewoon een API-call wanneer jullie data klaar hebben.

Het Connected-model is ook beschikbaar als jullie liever een API aanbieden waar wij periodiek data ophalen. Beide modellen kunnen naast elkaar bestaan voor verschillende bronnen.

**Authenticatie**

De authenticatie loopt via een API-key die we vanuit ons platform aanmaken. Jullie sturen die mee als `Authorization: Bearer sk-xxxxx` header. Simpel en veilig over TLS.

**Wat we van jullie nodig hebben**

De API werkt op twee niveaus: de **collectie** (knowledge base) en de **documenten** daarbinnen.

### Collectie-niveau

Bij het aanmaken of bijwerken van een collectie sturen jullie de volgende informatie mee:

| Veld          | Type   | Verplicht | Omschrijving                                              |
| ------------- | ------ | --------- | --------------------------------------------------------- |
| `source_id`   | string | ja        | Jullie unieke collectie-ID (voor updates en idempotentie) |
| `name`        | string | ja        | Naam van de collectie, bijv. "Wetten & Regelgeving"       |
| `description` | string | ja        | Omschrijving van de inhoud                                |
| `language`    | string | nee       | Primaire taal, bijv. `nl`                                 |
| `tags`        | list   | nee       | Labels, bijv. `["gemeente", "beleid"]`                    |
| `metadata`    | object | nee       | Vrij veld voor overige collectie-metadata                 |

### Document-niveau

Per document binnen de collectie:

_Verplicht:_

| Veld           | Type   | Omschrijving                                                  |
| -------------- | ------ | ------------------------------------------------------------- |
| `source_id`    | string | Jullie unieke document-ID (nodig voor updates/verwijderingen) |
| `filename`     | string | Originele bestandsnaam                                        |
| `content_type` | string | MIME-type, bijv. `application/pdf`, `text/plain`              |
| `text`         | string | De geparsede documenttekst                                    |

_Optioneel maar waardevol:_

| Veld          | Type   | Omschrijving                                                       |
| ------------- | ------ | ------------------------------------------------------------------ |
| `title`       | string | Documenttitel (vaak duidelijker dan de bestandsnaam)               |
| `source_url`  | string | Link naar het originele document (wordt getoond in bronvermelding) |
| `language`    | string | Taalcode, bijv. `nl`, `en`                                         |
| `author`      | string | Auteur van het document                                            |
| `created_at`  | string | Aanmaakdatum (ISO 8601)                                            |
| `modified_at` | string | Laatst gewijzigd (ISO 8601)                                        |
| `tags`        | list   | Labels/categorieën, bijv. `["beleid", "privacy"]`                  |
| `metadata`    | object | Vrij veld voor overige metadata                                    |

Hoe meer metadata, hoe beter de zoekresultaten en bronvermeldingen. Vooral `title`, `source_url` en `language` voegen veel waarde toe.

**Voorbeeld request**

```json
POST /api/v1/integrations/ingest
Authorization: Bearer sk-xxxxx

{
  "collection": {
    "source_id": "octobox-collection-456",
    "name": "Privacybeleid Gemeente Amsterdam",
    "description": "Alle beleidsdocumenten rondom privacy en AVG",
    "language": "nl",
    "tags": ["gemeente-amsterdam", "privacy", "avg"]
  },
  "documents": [
    {
      "source_id": "octobox-doc-12345",
      "filename": "privacybeleid-2026.pdf",
      "content_type": "application/pdf",
      "text": "De volledige geparsede tekst van het document...",
      "title": "Privacybeleid 2026",
      "source_url": "https://docs.example.com/privacybeleid-2026",
      "language": "nl",
      "author": "Juridische Zaken",
      "modified_at": "2026-02-15T10:30:00Z",
      "tags": ["beleid", "privacy"]
    },
    {
      "source_id": "octobox-doc-12346",
      "filename": "avg-handleiding-medewerkers.pdf",
      "content_type": "application/pdf",
      "text": "Tekst van het tweede document...",
      "title": "AVG Handleiding voor Medewerkers",
      "source_url": "https://docs.example.com/avg-handleiding",
      "language": "nl",
      "author": "HR",
      "modified_at": "2026-01-20T14:00:00Z",
      "tags": ["avg", "handleiding"]
    }
  ]
}
```

Als de collectie al bestaat (op basis van `source_id`), worden de nieuwe documenten toegevoegd aan de bestaande collectie. Documenten met een bestaand `source_id` worden bijgewerkt in plaats van gedupliceerd.

**Wat we achter de schermen doen**

Na ontvangst van jullie data:

1. **Collectie aanmaken** — we maken een knowledge base aan (of updaten een bestaande) met de meegegeven naam en beschrijving
2. **Chunking** — we splitsen de tekst op in optimale stukken voor retrieval
3. **Embedding** — we genereren vector-embeddings zodat de content semantisch doorzoekbaar wordt
4. **Opslag** — chunks en embeddings worden opgeslagen in Weaviate
5. **Beschikbaar** — de knowledge base is direct koppelbaar aan AI-modellen in het platform

Jullie hoeven je dus alleen bezig te houden met het parsen en aanleveren van de tekst — de hele RAG-pipeline (chunking, embedding, opslag, retrieval) nemen wij voor onze rekening.

**Wat ik graag van jullie zou horen**

- Welke metadata hebben jullie beschikbaar op collectie- en documentniveau? Dan stemmen we het schema af.
- Sturen jullie documenten per stuk of in batches?
- Hebben jullie behoefte aan een callback/webhook als de verwerking klaar is, of is een synchrone response voldoende?
- Hoe gaan jullie om met verwijderde documenten — moeten wij die ook opruimen?
- Is het Ingest-model (jullie pushen naar ons) de voorkeur, of willen jullie liever een API aanbieden die wij periodiek bevragen (Connected-model)?

Laat het weten als je vragen hebt of als het handig is om dit even in een call door te nemen.

Groet,
Lex
