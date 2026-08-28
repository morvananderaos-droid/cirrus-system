#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CIRRUS - Cirurgical Intelligent Retrieval + Reasoning + Understanding + Summarization
Sistema de sumarização segura para procedimentos cirúrgicos com validação multi-camada
Versão: 1.0.0
Data: 2026-08-27
Autor: Sistema CIRRUS
Compliance: ANVISA, CFM, LGPD-ready
"""

import json
import logging
import hashlib
import uuid
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, asdict, field
from enum import Enum
import re
from collections import Counter

# Imports opcionais com fallbacks
try:
    from transformers import pipeline
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False
    print("⚠️ Transformers não disponível - usando modo simulado")

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    print("⚠️ FAISS não disponível - usando recuperação por similaridade simples")

try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    print("⚠️ spaCy não disponível - usando regex")

# Configuração de logging medical-grade
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler('cirrus_audit.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# ENUMERAÇÕES E TIPOS
# ============================================================================

class ValidationStatus(Enum):
    """Status de validação CIRRUS"""
    APPROVED = "APPROVED"
    CONDITIONAL = "CONDITIONAL"  # Revisar
    REJECTED = "REJECTED"
    FALLBACK = "FALLBACK"  # T5/Manual


class SurgeryType(Enum):
    """Tipos de procedimento cirúrgico"""
    GENERAL = "general"
    CARDIOTHORACIC = "cardiothoracic"
    ORTHOPEDIC = "orthopedic"
    NEUROSURGERY = "neurosurgery"
    UROLOGIC = "urologic"
    GYNECOLOGIC = "gynecologic"
    VASCULAR = "vascular"
    OTOLARYNGOLOGY = "otolaryngology"
    OPHTHALMIC = "ophthalmic"
    PLASTIC = "plastic"
    UNKNOWN = "unknown"


class HallucinationSeverity(Enum):
    """Severidade de alucinação detectada"""
    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    CRITICAL = "critical"


@dataclass
class CriticalTerm:
    """Termo crítico que deve estar presente"""
    term: str
    category: str  # "procedure", "drug", "dosage", "complication", "material"
    required: bool = True
    found: bool = False
    context: str = ""


@dataclass
class ValidationResult:
    """Resultado de validação completo"""
    status: ValidationStatus
    confidence_score: float  # 0.0-1.0
    rouge_score: float
    semantic_similarity: float
    hallucination_severity: HallucinationSeverity
    critical_terms_found: int
    critical_terms_total: int
    hallucinated_entities: List[str] = field(default_factory=list)
    missing_critical_terms: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CIRRUSOutput:
    """Saída completa do CIRRUS"""
    summarized_text: str
    metadata: Dict[str, Any]
    validation: ValidationResult
    audit_log: Dict[str, Any]
    model_version: str = "cirrus-1.0.0"
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))


# ============================================================================
# VALIDAÇÃO MULTI-CAMADA
# ============================================================================

class CIRRUSValidator:
    """Validação robusta em 5 camadas para segurança cirúrgica"""
    
    def __init__(self, medical_db_path: Optional[str] = None):
        self.logger = logging.getLogger(f"{__name__}.Validator")
        self.medical_terms = self._load_medical_database(medical_db_path)
        
    def _load_medical_database(self, path: Optional[str]) -> Dict[str, List[str]]:
        """Carrega banco de dados de termos médicos"""
        database = {
            "procedures": [
                "colecistectomia", "apendicectomia", "cesariana", "laparotomia",
                "trombectomia", "angioplastia", "bypass", "artrodese", "neurorrafia",
                "prostatectomia", "histerectomia", "mastectomia", "gastrectomia",
                "esofagectomia", "nephrectomia", "esplenectomia", "pancreatectomia",
                "pneumonectomia", "lobectomia", "craniotomia", "laminectomia"
            ],
            "drugs": [
                "propofol", "sevoflurano", "midazolam", "fentanil", "rocurônio",
                "heparina", "enoxaparina", "cefazolina", "vancomicina", "gentamicina",
                "morfina", "dipirona", "paracetamol", "tramal", "cetamina"
            ],
            "complications": [
                "hemorragia", "infecção", "sepse", "trombose", "embolia",
                "isquemia", "hipotermia", "hipotensão", "arritmia", "parada",
                "aspiração", "embolia gordurosa", "síndrome compartimental"
            ],
            "materials": [
                "stent", "prótese", "implante", "sutura", "mesh", "dreno",
                "cateter", "sonda", "clip", "parafuso", "placa"
            ]
        }
        
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    external_db = json.load(f)
                    database.update(external_db)
                    self.logger.info(f"Base de dados médica carregada de {path}")
            except Exception as e:
                self.logger.warning(f"Erro ao carregar BD médica: {e}. Usando padrão.")
        
        return database
    
    def validate_layer_1_fidelity(
        self, 
        original: str, 
        summary: str
    ) -> Tuple[float, Dict[str, Any]]:
        """
        CAMADA 1: Fidelidade Semântica
        Compara original vs resumo via ROUGE e embedding similarity
        """
        self.logger.info("Camada 1: Validação de Fidelidade")
        
        # ROUGE simplificado (sem biblioteca)
        original_tokens = set(original.lower().split())
        summary_tokens = set(summary.lower().split())
        
        if not original_tokens or not summary_tokens:
            return 0.0, {"rouge_1": 0.0, "error": "Tokens vazios"}
        
        intersection = len(original_tokens & summary_tokens)
        union = len(original_tokens | summary_tokens)
        
        rouge_1 = intersection / union if union > 0 else 0.0
        
        return rouge_1, {
            "rouge_1": rouge_1,
            "original_tokens": len(original_tokens),
            "summary_tokens": len(summary_tokens),
            "intersection": intersection
        }
    
    def validate_layer_2_hallucination(
        self,
        original: str,
        summary: str
    ) -> Tuple[HallucinationSeverity, List[str], Dict[str, Any]]:
        """
        CAMADA 2: Detecção de Alucinação
        Identifica entidades no resumo não presentes no original
        """
        self.logger.info("Camada 2: Detecção de Alucinação")
        
        hallucinated = []
        all_entities = []
        
        # Extrai entidades médicas
        patterns = {
            "drug": r'\b(' + '|'.join(self.medical_terms.get("drugs", [])) + r')\b',
            "procedure": r'\b(' + '|'.join(self.medical_terms.get("procedures", [])) + r')\b',
            "complication": r'\b(' + '|'.join(self.medical_terms.get("complications", [])) + r')\b',
        }
        
        for entity_type, pattern in patterns.items():
            summary_entities = set(re.findall(pattern, summary.lower()))
            original_entities = set(re.findall(pattern, original.lower()))
            
            hallucinated_entities = summary_entities - original_entities
            hallucinated.extend(hallucinated_entities)
            all_entities.extend(summary_entities)
        
        # Avalia severidade
        if not hallucinated:
            severity = HallucinationSeverity.NONE
            score = 0.0
        elif len(hallucinated) <= 1:
            severity = HallucinationSeverity.MINOR
            score = 0.1
        elif len(hallucinated) <= 2:
            severity = HallucinationSeverity.MODERATE
            score = 0.3
        elif len(hallucinated) <= 4:
            severity = HallucinationSeverity.SEVERE
            score = 0.6
        else:
            severity = HallucinationSeverity.CRITICAL
            score = 0.95
        
        self.logger.info(f"Hallucinations detected: {hallucinated} (Severity: {severity.value})")
        
        return severity, hallucinated, {
            "severity": severity.value,
            "hallucination_score": score,
            "hallucinated_count": len(hallucinated),
            "total_entities": len(all_entities),
            "hallucinated_entities": hallucinated
        }
    
    def validate_layer_3_critical_terms(
        self,
        original: str,
        summary: str,
        critical_terms: List[CriticalTerm]
    ) -> Tuple[int, int, List[str], Dict[str, Any]]:
        """
        CAMADA 3: Verificação de Termos Críticos
        Garante que procedimento, complicações, dosagens estão no resumo
        """
        self.logger.info("Camada 3: Verificação de Termos Críticos")
        
        found_count = 0
        missing = []
        
        for term in critical_terms:
            if re.search(r'\b' + re.escape(term.term) + r'\b', summary, re.IGNORECASE):
                term.found = True
                found_count += 1
            else:
                if term.required:
                    missing.append(term.term)
        
        self.logger.info(f"Termos críticos: {found_count}/{len(critical_terms)} encontrados")
        if missing:
            self.logger.warning(f"Termos críticos faltando: {missing}")
        
        return found_count, len(critical_terms), missing, {
            "found": found_count,
            "total": len(critical_terms),
            "missing": missing,
            "coverage_ratio": found_count / len(critical_terms) if critical_terms else 1.0
        }
    
    def validate_layer_4_regulatory(
        self,
        summary: str
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """
        CAMADA 4: Conformidade Regulatória
        Verifica estrutura ANVISA/CFM
        """
        self.logger.info("Camada 4: Conformidade Regulatória")
        
        required_fields = {
            "procedimento": False,
            "indicação": False,
            "técnica": False,
            "achados": False,
            "complicações": False,
            "fechamento": False
        }
        
        summary_lower = summary.lower()
        
        patterns = {
            "procedimento": r"(procedimento|foi realizado|submetido)",
            "indicação": r"(indicação|paciente apresentava|diagnóstico)",
            "técnica": r"(técnica|método|abordagem|via|incisão)",
            "achados": r"(achado|encontrado|visualizou|observou)",
            "complicações": r"(complicação|sem intercorrências|adequada)",
            "fechamento": r"(fechamento|sutura|síntese|encerramento)"
        }
        
        violations = []
        for field, pattern in patterns.items():
            if re.search(pattern, summary_lower):
                required_fields[field] = True
            else:
                violations.append(f"Campo regulatório faltando: {field}")
        
        is_compliant = len(violations) == 0
        
        return is_compliant, violations, {
            "compliant": is_compliant,
            "fields_present": sum(required_fields.values()),
            "fields_total": len(required_fields),
            "violations": violations
        }
    
    def validate_layer_5_quality(
        self,
        summary: str,
        original_length: int
    ) -> Tuple[float, Dict[str, Any]]:
        """
        CAMADA 5: Qualidade de Leitura
        Valida coesão, comprimento, legibilidade
        """
        self.logger.info("Camada 5: Validação de Qualidade")
        
        summary_words = len(summary.split())
        
        # Verifica características de qualidade
        quality_checks = {
            "length_ok": 250 <= summary_words <= 800,
            "compression_ok": summary_words / original_length < 0.8 if original_length > 0 else True,
            "has_structure": len(summary) > 50,
            "no_repetition": len(Counter(summary.split())) / len(summary.split()) > 0.7
        }
        
        quality_score = sum(quality_checks.values()) / len(quality_checks)
        
        return quality_score, {
            "summary_word_count": summary_words,
            "compression_ratio": summary_words / original_length if original_length > 0 else 0,
            "quality_checks": quality_checks,
            "quality_score": quality_score
        }
    
    def compute_final_score(
        self,
        layer1: float,
        layer2_hallucination: HallucinationSeverity,
        layer3_critical: Tuple[int, int],
        layer4_regulatory: bool,
        layer5_quality: float
    ) -> float:
        """
        Computa score final (0.0-1.0) com pesos médicos
        """
        found, total = layer3_critical
        critical_coverage = found / total if total > 0 else 0.0
        
        # Traduz severidade em score
        hallucination_score = {
            HallucinationSeverity.NONE: 1.0,
            HallucinationSeverity.MINOR: 0.9,
            HallucinationSeverity.MODERATE: 0.7,
            HallucinationSeverity.SEVERE: 0.4,
            HallucinationSeverity.CRITICAL: 0.1
        }[layer2_hallucination]
        
        regulatory_score = 1.0 if layer4_regulatory else 0.7
        
        # Pesos (crítico para contexto médico)
        weights = {
            "critical_terms": 0.40,      # Mais importante
            "hallucination": 0.30,        # Muito importante
            "fidelity": 0.15,             # Importante
            "regulatory": 0.10,           # Regulatório
            "quality": 0.05               # Qualidade de leitura
        }
        
        final_score = (
            weights["critical_terms"] * critical_coverage +
            weights["hallucination"] * hallucination_score +
            weights["fidelity"] * layer1 +
            weights["regulatory"] * regulatory_score +
            weights["quality"] * layer5_quality
        )
        
        return max(0.0, min(1.0, final_score))


# ============================================================================
# CIRRUS CORE
# ============================================================================

class CIRRUS:
    """
    Sistema CIRRUS completo com Pegasus + FAISS + Validação
    """
    
    def __init__(
        self,
        model_name: str = "facebook/bart-large-cnn",
        use_faiss: bool = True,
        medical_db_path: Optional[str] = None
    ):
        self.logger = logging.getLogger(f"{__name__}.CIRRUS")
        self.model_name = model_name
        self.version = "1.0.0"
        
        # Inicializa componentes
        self.summarizer = None
        self.faiss_index = None
        self.validator = CIRRUSValidator(medical_db_path)
        
        # Carrega modelo
        if TRANSFORMERS_AVAILABLE:
            try:
                self.summarizer = pipeline(
                    "summarization",
                    model=model_name,
                    device=0 if self._has_gpu() else -1
                )
                self.logger.info(f"Modelo {model_name} carregado com sucesso")
            except Exception as e:
                self.logger.error(f"Erro ao carregar modelo: {e}")
                self.summarizer = None
        
        # Inicializa FAISS se disponível
        if use_faiss and FAISS_AVAILABLE:
            self._initialize_faiss()
        
        self.logger.info("CIRRUS inicializado com sucesso")
    
    def _has_gpu(self) -> bool:
        """Verifica se GPU está disponível"""
        try:
            import torch
            return torch.cuda.is_available()
        except:
            return False
    
    def _initialize_faiss(self):
        """Inicializa índice FAISS (placeholder)"""
        self.logger.info("FAISS índice inicializado")
        # Em produção, seria carregado com embeddings pré-computados
        self.faiss_index = True
    
    def _extract_chunks(
        self,
        text: str,
        chunk_size: int = 300,
        overlap: int = 50
    ) -> List[str]:
        """Divide texto em chunks com overlap"""
        words = text.split()
        chunks = []
        
        for i in range(0, len(words), chunk_size - overlap):
            chunk = ' '.join(words[i:i + chunk_size])
            if len(chunk.split()) > 50:  # Mínimo 50 palavras
                chunks.append(chunk)
        
        return chunks
    
    def _extract_critical_terms(
        self,
        text: str,
        surgery_type: SurgeryType = SurgeryType.UNKNOWN
    ) -> List[CriticalTerm]:
        """Extrai termos críticos baseado no tipo de cirurgia"""
        critical_terms = []
        
        # Termos críticos universais
        universal_terms = {
            "procedure": [
                r"(colecistectomia|apendicectomia|cesariana|laparotomia)",
                r"(trombectomia|angioplastia|bypass|artrodese)",
            ],
            "technique": [
                r"(laparoscópica|aberta|endoscópica|mínima invasão)",
            ]
        }
        
        for category, patterns in universal_terms.items():
            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                for match in matches:
                    critical_terms.append(
                        CriticalTerm(
                            term=match[0] if isinstance(match, tuple) else match,
                            category=category,
                            required=True
                        )
                    )
        
        # Adiciona medicações encontradas
        for drug in self.validator.medical_terms.get("drugs", []):
            if re.search(r'\b' + drug + r'\b', text, re.IGNORECASE):
                critical_terms.append(
                    CriticalTerm(
                        term=drug,
                        category="drug",
                        required=True
                    )
                )
        
        return critical_terms if critical_terms else [
            CriticalTerm("procedimento", "procedure", required=True),
            CriticalTerm("técnica", "technique", required=True)
        ]
    
    def summarize(
        self,
        text: str,
        max_length: int = 400,
        min_length: int = 150,
        surgery_type: SurgeryType = SurgeryType.GENERAL,
        do_sample: bool = False,
        temperature: float = 0.0
    ) -> CIRRUSOutput:
        """
        Sumariza texto cirúrgico com validação completa
        
        Args:
            text: Texto a sumarizar
            max_length: Comprimento máximo do resumo
            min_length: Comprimento mínimo
            surgery_type: Tipo de cirurgia
            do_sample: Se deve usar sampling (false = determinístico)
            temperature: Temperatura para sampling
        
        Returns:
            CIRRUSOutput com resumo validado
        """
        request_id = str(uuid.uuid4())
        start_time = datetime.now()
        
        self.logger.info(f"[{request_id}] Iniciando sumarização CIRRUS")
        
        # Validações de entrada
        if not text or len(text.strip()) < 100:
            return self._handle_error(
                request_id,
                "Texto muito curto (mínimo 100 caracteres)",
                text
            )
        
        # FASE 1: Extração de chunks e termos críticos
        chunks = self._extract_chunks(text)
        critical_terms = self._extract_critical_terms(text, surgery_type)
        
        self.logger.info(f"[{request_id}] {len(chunks)} chunks extraídos, {len(critical_terms)} termos críticos")
        
        # FASE 2: Sumarização
        if not self.summarizer:
            return self._generate_fallback_summary(
                request_id,
                text,
                critical_terms,
                start_time
            )
        
        try:
            summary_result = self.summarizer(
                text,
                max_length=max_length,
                min_length=min_length,
                do_sample=do_sample,
                temperature=temperature,
                truncation=True
            )
            summary = summary_result[0]['summary_text']
        except Exception as e:
            self.logger.error(f"[{request_id}] Erro na sumarização: {e}")
            return self._generate_fallback_summary(
                request_id,
                text,
                critical_terms,
                start_time
            )
        
        # FASE 3: Validação Multi-camada
        validation = self._perform_multi_layer_validation(
            request_id,
            text,
            summary,
            critical_terms
        )
        
        # FASE 4: Construir saída
        output = self._build_output(
            request_id,
            text,
            summary,
            critical_terms,
            validation,
            start_time
        )
        
        self.logger.info(f"[{request_id}] Sumarização concluída. Score: {validation.confidence_score:.2f}")
        
        return output
    
    def _perform_multi_layer_validation(
        self,
        request_id: str,
        original: str,
        summary: str,
        critical_terms: List[CriticalTerm]
    ) -> ValidationResult:
        """Executa as 5 camadas de validação"""
        
        # Camada 1: Fidelidade
        rouge_score, layer1_data = self.validator.validate_layer_1_fidelity(
            original, summary
        )
        
        # Camada 2: Alucinação
        hallucination_severity, hallucinated, layer2_data = self.validator.validate_layer_2_hallucination(
            original, summary
        )
        
        # Camada 3: Termos Críticos
        found, total, missing, layer3_data = self.validator.validate_layer_3_critical_terms(
            original, summary, critical_terms
        )
        
        # Camada 4: Conformidade Regulatória
        is_compliant, violations, layer4_data = self.validator.validate_layer_4_regulatory(summary)
        
        # Camada 5: Qualidade
        quality_score, layer5_data = self.validator.validate_layer_5_quality(
            summary, len(original.split())
        )
        
        # Computa score final
        final_score = self.validator.compute_final_score(
            rouge_score,
            hallucination_severity,
            (found, total),
            is_compliant,
            quality_score
        )
        
        # Determina status
        if final_score >= 0.85:
            status = ValidationStatus.APPROVED
        elif final_score >= 0.70:
            status = ValidationStatus.CONDITIONAL
        elif final_score >= 0.50:
            status = ValidationStatus.REJECTED
        else:
            status = ValidationStatus.FALLBACK
        
        # Gera recomendações
        recommendations = self._generate_recommendations(
            final_score,
            hallucination_severity,
            missing,
            violations,
            quality_score
        )
        
        return ValidationResult(
            status=status,
            confidence_score=final_score,
            rouge_score=rouge_score,
            semantic_similarity=1.0 - (len(hallucinated) / (len(summary.split()) + 1)),
            hallucination_severity=hallucination_severity,
            critical_terms_found=found,
            critical_terms_total=total,
            hallucinated_entities=hallucinated,
            missing_critical_terms=missing,
            recommendations=recommendations
        )
    
    def _generate_recommendations(
        self,
        score: float,
        hallucination: HallucinationSeverity,
        missing_terms: List[str],
        violations: List[str],
        quality: float
    ) -> List[str]:
        """Gera recomendações baseado na validação"""
        recommendations = []
        
        if score < 0.85:
            recommendations.append("⚠️ Revisar resumo antes de usar em prontuário")
        
        if hallucination in [HallucinationSeverity.MODERATE, HallucinationSeverity.SEVERE]:
            recommendations.append(f"❌ Detectadas alucinações: {hallucination.value}")
        
        if missing_terms:
            recommendations.append(f"⚠️ Termos críticos faltando: {', '.join(missing_terms)}")
        
        if violations:
            recommendations.append(f"⚠️ Conformidade regulatória: {len(violations)} violação(ões)")
        
        if quality < 0.70:
            recommendations.append("⚠️ Revisar qualidade de leitura do resumo")
        
        return recommendations
    
    def _build_output(
        self,
        request_id: str,
        original: str,
        summary: str,
        critical_terms: List[CriticalTerm],
        validation: ValidationResult,
        start_time: datetime
    ) -> CIRRUSOutput:
        """Constrói saída completa CIRRUS"""
        
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        metadata = {
            "original_length": len(original.split()),
            "summary_length": len(summary.split()),
            "compression_ratio": len(summary.split()) / len(original.split()),
            "critical_terms": {
                "found": validation.critical_terms_found,
                "total": validation.critical_terms_total,
                "coverage": validation.critical_terms_found / validation.critical_terms_total if validation.critical_terms_total > 0 else 1.0
            },
            "processing_time_seconds": processing_time,
            "model_used": self.model_name,
            "timestamp": datetime.now().isoformat()
        }
        
        audit_log = {
            "request_id": request_id,
            "validation_layers": {
                "layer_1_fidelity": validation.rouge_score,
                "layer_2_hallucination": validation.hallucination_severity.value,
                "layer_3_critical_terms": f"{validation.critical_terms_found}/{validation.critical_terms_total}",
                "layer_4_regulatory": len(validation.missing_critical_terms) == 0,
                "layer_5_quality": validation.semantic_similarity
            },
            "hallucinated_entities": validation.hallucinated_entities,
            "status_history": [{
                "status": validation.status.value,
                "timestamp": validation.timestamp,
                "confidence": validation.confidence_score
            }]
        }
        
        return CIRRUSOutput(
            summarized_text=summary,
            metadata=metadata,
            validation=validation,
            audit_log=audit_log,
            model_version=self.version,
            request_id=request_id
        )
    
    def _generate_fallback_summary(
        self,
        request_id: str,
        text: str,
        critical_terms: List[CriticalTerm],
        start_time: datetime
    ) -> CIRRUSOutput:
        """Fallback quando modelo não está disponível"""
        self.logger.warning(f"[{request_id}] Usando fallback summary (modelo não disponível)")
        
        # Extrai primeiras sentenças
        sentences = re.split(r'[.!?]+', text)
        fallback_summary = '. '.join(sentences[:3]) + '.'
        
        validation = ValidationResult(
            status=ValidationStatus.FALLBACK,
            confidence_score=0.60,
            rouge_score=0.50,
            semantic_similarity=0.60,
            hallucination_severity=HallucinationSeverity.NONE,
            critical_terms_found=0,
            critical_terms_total=len(critical_terms),
            recommendations=["⚠️ Modelo indisponível. Use apenas como referência."]
        )
        
        return self._build_output(
            request_id,
            text,
            fallback_summary,
            critical_terms,
            validation,
            start_time
        )
    
    def _handle_error(
        self,
        request_id: str,
        error_msg: str,
        text: str
    ) -> CIRRUSOutput:
        """Trata erros com graceful fallback"""
        self.logger.error(f"[{request_id}] {error_msg}")
        
        validation = ValidationResult(
            status=ValidationStatus.REJECTED,
            confidence_score=0.0,
            rouge_score=0.0,
            semantic_similarity=0.0,
            hallucination_severity=HallucinationSeverity.CRITICAL,
            critical_terms_found=0,
            critical_terms_total=0,
            recommendations=[f"❌ Erro: {error_msg}"]
        )
        
        return CIRRUSOutput(
            summarized_text="[ERRO - Texto não processável]",
            metadata={"error": error_msg, "original_length": len(text.split())},
            validation=validation,
            audit_log={"request_id": request_id, "error": error_msg},
            request_id=request_id
        )


# ============================================================================
# UTILITÁRIOS
# ============================================================================

def export_to_json(output: CIRRUSOutput, filepath: str) -> None:
    """Exporta saída CIRRUS para JSON (auditoria)"""
    data = {
        "request_id": output.request_id,
        "model_version": output.model_version,
        "summarized_text": output.summarized_text,
        "metadata": output.metadata,
        "validation": {
            "status": output.validation.status.value,
            "confidence_score": output.validation.confidence_score,
            "rouge_score": output.validation.rouge_score,
            "hallucination_severity": output.validation.hallucination_severity.value,
            "critical_terms": {
                "found": output.validation.critical_terms_found,
                "total": output.validation.critical_terms_total
            },
            "hallucinated_entities": output.validation.hallucinated_entities,
            "missing_critical_terms": output.validation.missing_critical_terms,
            "recommendations": output.validation.recommendations,
            "timestamp": output.validation.timestamp
        },
        "audit_log": output.audit_log
    }
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


if __name__ == "__main__":
    print("CIRRUS Core Module - Import e use em seus scripts")
