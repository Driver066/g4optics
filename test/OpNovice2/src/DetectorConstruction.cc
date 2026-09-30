//
// ********************************************************************
// * License and Disclaimer                                           *
// *                                                                  *
// * The  Geant4 software  is  copyright of the Copyright Holders  of *
// * the Geant4 Collaboration.  It is provided  under  the terms  and *
// * conditions of the Geant4 Software License,  included in the file *
// * LICENSE and available at  http://cern.ch/geant4/license .  These *
// * include a list of copyright holders.                             *
// *                                                                  *
// * Neither the authors of this software system, nor their employing *
// * institutes,nor the agencies providing financial support for this *
// * work  make  any representation or  warranty, express or implied, *
// * regarding  this  software system or assume any liability for its *
// * use.  Please see the license in the file  LICENSE  and URL above *
// * for the full disclaimer and the limitation of liability.         *
// *                                                                  *
// * This  code  implementation is the result of  the  scientific and *
// * technical work of the GEANT4 collaboration.                      *
// * By using,  copying,  modifying or  distributing the software (or *
// * any work based  on the software)  you  agree  to acknowledge its *
// * use  in  resulting  scientific  publications,  and indicate your *
// * acceptance of all terms of the Geant4 Software license.          *
// ********************************************************************
//
/// \file optical/OpNovice2/src/DetectorConstruction.cc
/// \brief Implementation of the DetectorConstruction class
//
//
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#include "DetectorConstruction.hh"

#include "DetectorMessenger.hh"
#include "W08PhotonDiagnostics.hh"

#include "G4Box.hh"
#include "G4DisplacedSolid.hh"
#include "G4Element.hh"
#include "G4IntersectionSolid.hh"
#include "G4LogicalBorderSurface.hh"
#include "G4LogicalSkinSurface.hh"
#include "G4LogicalVolume.hh"
#include "G4Material.hh"
#include "G4MaterialPropertiesTable.hh"
#include "G4NistManager.hh"
#include "G4OpticalSurface.hh"
#include "G4PhysicalConstants.hh"
#include "G4PVPlacement.hh"
#include "G4Sphere.hh"
#include "G4SubtractionSolid.hh"
#include "G4SystemOfUnits.hh"
#include "G4ThreeVector.hh"
#include "G4VSolid.hh"

#include "G4Colour.hh"
#include "G4VisAttributes.hh"

#include <algorithm>
#include <array>
#include <cmath>
#include <string>

namespace {
// These checks only read resolved objects. In particular, they do not query a
// navigator, sample a solid, interpolate a physics vector, or draw randoms.
void RequireW08(G4bool condition, const G4String& detail)
{
  if (!condition) {
    G4ExceptionDescription message;
    message << "W08 photon-loss diagnostics require the fixed W08 baseline. "
            << detail;
    G4Exception("DetectorConstruction::ValidateW08PhotonLossConfiguration",
                "OpNovice2_W08_001", FatalException, message);
  }
}

G4bool W08Equal(G4double actual, G4double expected)
{
  return std::isfinite(actual) &&
         std::abs(actual - expected) <= 1.e-12 * std::max(1., std::abs(expected));
}

G4bool W08SamePosition(const G4ThreeVector& actual, const G4ThreeVector& expected)
{
  return W08Equal(actual.x() / mm, expected.x() / mm) &&
         W08Equal(actual.y() / mm, expected.y() / mm) &&
         W08Equal(actual.z() / mm, expected.z() / mm);
}

void CheckW08PropertyNames(const G4MaterialPropertiesTable* table,
                          const G4String& label,
                          const std::vector<G4String>& allowedProperties,
                          const std::vector<G4String>& allowedConstants)
{
  RequireW08(table != nullptr, label + " has no material properties table.");
  if (!table) return;
  const auto& values = table->GetProperties();
  const auto& names = table->GetMaterialPropertyNames();
  for (std::size_t i = 0; i < values.size(); ++i) {
    if (values[i]) {
      RequireW08(i < names.size(), label + " has an unnamed material property.");
      if (i >= names.size()) return;
      RequireW08(std::find(allowedProperties.begin(), allowedProperties.end(), names[i]) !=
                   allowedProperties.end(),
                 label + " has an extra material property: " + names[i]);
    }
  }
  const auto& constants = table->GetConstProperties();
  const auto& constantNames = table->GetMaterialConstPropertyNames();
  for (std::size_t i = 0; i < constants.size(); ++i) {
    if (constants[i].second) {
      RequireW08(i < constantNames.size(), label + " has an unnamed material constant.");
      if (i >= constantNames.size()) return;
      RequireW08(std::find(allowedConstants.begin(), allowedConstants.end(), constantNames[i]) !=
                   allowedConstants.end(),
                 label + " has an extra material constant: " + constantNames[i]);
    }
  }
}

void CheckW08ConstantVector(const G4MaterialPropertiesTable* table,
                           const G4String& label,
                           const G4String& name,
                           G4double expected)
{
  const auto* property = table ? table->GetProperty(name) : nullptr;
  RequireW08(property && property->GetVectorLength() == 2,
             label + " " + name + " must have the two W08 endpoints.");
  if (!property || property->GetVectorLength() != 2) return;
  RequireW08(W08Equal(property->Energy(0) / eV, 2.) &&
               W08Equal(property->Energy(1) / eV, 3.3) &&
               W08Equal((*property)[0], expected) && W08Equal((*property)[1], expected),
             label + " " + name + " differs from its W08 energy/value pair.");
}

void CheckW08Constant(const G4MaterialPropertiesTable* table,
                     const G4String& name,
                     G4double expected)
{
  RequireW08(table && table->ConstPropertyExists(name), "EJ-200 is missing " + name);
  if (!table || !table->ConstPropertyExists(name)) return;
  RequireW08(W08Equal(table->GetConstProperty(name), expected),
             "EJ-200 " + name + " differs from W08.");
}
}  // namespace

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

DetectorConstruction::DetectorConstruction()
  : G4VUserDetectorConstruction(), fDetectorMessenger(nullptr)
{
  fTankMPT = new G4MaterialPropertiesTable();
  fWorldMPT = new G4MaterialPropertiesTable();
  fSurfaceMPT = new G4MaterialPropertiesTable();
  // The properties table of SiPM
  fSiPMMPT = new G4MaterialPropertiesTable();
  fGreaseMPT = new G4MaterialPropertiesTable();

  fSurface = new G4OpticalSurface("Surface");
  fSurface->SetType(dielectric_dielectric);
  fSurface->SetFinish(ground);
  fSurface->SetModel(unified);
  fSurface->SetMaterialPropertiesTable(fSurfaceMPT);

  auto nist = G4NistManager::Instance();
  fTankMaterial = nist->FindOrBuildMaterial("G4_PLASTIC_SC_VINYLTOLUENE");
  fWorldMaterial = nist->FindOrBuildMaterial("G4_AIR");

  // SAE 304 stainless steel baseline used by the realistic-neutron study.
  // Keep the study composition explicit rather than relying on a Geant4
  // material alias whose alloy fractions may differ across releases.
  fAbsorberMaterial = new G4Material("StainlessSteelSAE304", 7.9 * g / cm3, 3);
  fAbsorberMaterial->AddElement(nist->FindOrBuildElement("Fe"), 0.74);
  fAbsorberMaterial->AddElement(nist->FindOrBuildElement("Cr"), 0.18);
  fAbsorberMaterial->AddElement(nist->FindOrBuildElement("Ni"), 0.08);

  // The material of SiPM
  fSiPMMaterial = nist->FindOrBuildMaterial("G4_Si");

  // EJ-550 optical-grade silicone grease is represented with a simple
  // silicone-like composition proxy. Eljen specifies a specific gravity of
  // 1.06; optical constants are supplied by macro.
  fGreaseMaterial = new G4Material("EJ550_Grease", 1.06 * g / cm3, 4);
  fGreaseMaterial->AddElement(nist->FindOrBuildElement("C"), 2);
  fGreaseMaterial->AddElement(nist->FindOrBuildElement("H"), 6);
  fGreaseMaterial->AddElement(nist->FindOrBuildElement("O"), 1);
  fGreaseMaterial->AddElement(nist->FindOrBuildElement("Si"), 1);

  fDetectorMessenger = new DetectorMessenger(this);

  // ----- SiPM -----
  const G4int nEntries = 2;
  G4double photonEnergy[nEntries] = {2.0 * eV, 3.3 * eV};

  // Toy SiPM/silicon optical properties.
  // This is enough for first-pass photon entry + detection.
  G4double siRIndex[nEntries] = {4.0, 4.0};

  // Strong absorption once photons enter SiPM.
  G4double siAbsLength[nEntries] = {1.0 * um, 1.0 * um};

  fSiPMMPT->AddProperty("RINDEX", photonEnergy, siRIndex, nEntries);
  fSiPMMPT->AddProperty("ABSLENGTH", photonEnergy, siAbsLength, nEntries);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

DetectorConstruction::~DetectorConstruction()
{
  delete fTankMPT;
  delete fWorldMPT;
  delete fSurfaceMPT;
  // Frees the memory of SiPM
  delete fSiPMMPT;
  delete fGreaseMPT;
  delete fSurface;
  delete fDetectorMessenger;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

G4VPhysicalVolume* DetectorConstruction::Construct()
{
  fWorld = nullptr;
  fTank = nullptr;
  fAbsorber = nullptr;
  fAbsorber_LV = nullptr;
  fTanks.clear();
  fAbsorbers.clear();
  fSiPM = nullptr;
  fSiPMs.clear();
  fGrease = nullptr;
  fGrease_LV = nullptr;

  if ((fStackModel == "v2" && !fStackEnabled) ||
      (fStackReadoutGapExplicit && (fStackModel != "v2" || !fStackEnabled))) {
    G4Exception("DetectorConstruction::Construct", "OpNovice2_Stack_007", FatalException,
                "The v2 model/readoutGap requires an enabled v2 longitudinal stack.");
  }
  if (fStackEnabled) {
    ValidateStackConfiguration();
    if (IsStackV2()) {
      G4cout << "Stack v2: core_length=" << GetStackCoreLength() / mm
             << " mm, readout_gap=" << GetStackReadoutGap() / mm
             << " mm, internal_gaps=9, copy_stride=4, active_sensors_per_layer="
             << GetActiveSensorsPerLayer() << ", final_layer_boundary=World" << G4endl;
    }
  }

  fTankMaterial->SetMaterialPropertiesTable(fTankMPT);
  fTankMaterial->GetIonisation()->SetBirksConstant(0.126 * mm / MeV);

  fWorldMaterial->SetMaterialPropertiesTable(fWorldMPT);

  // SiPM Properties Table
  fSiPMMaterial->SetMaterialPropertiesTable(fSiPMMPT);
  fGreaseMaterial->SetMaterialPropertiesTable(fGreaseMPT);

  // ------------- Volumes --------------
  // The experimental Hall
  auto world_box = new G4Box("World", fExpHall_x, fExpHall_y, fExpHall_z);

  fWorld_LV = new G4LogicalVolume(world_box, fWorldMaterial, "World");

  G4VPhysicalVolume* world_PV =
    new G4PVPlacement(nullptr, G4ThreeVector(), fWorld_LV, "World", nullptr, false, 0);
  fWorld = world_PV;

  // The tank
  auto tank_box = new G4Box("Tank_Box", fTank_x, fTank_y, fTank_z);
  G4VSolid* tank_solid = tank_box;

  if (fBottomCavityEnabled && fDimpleEnabled) {
    G4ExceptionDescription msg;
    msg << "/opnovice2/dimple/enabled cannot be combined with "
        << "/opnovice2/tank/bottomCavity true. Use one cavity model at a time.";
    G4Exception("DetectorConstruction::Construct",
                "OpNovice2_Dimple_001",
                FatalException,
                msg);
  }

  if (fBottomCavityEnabled) {
    auto bottomCavitySphere = new G4Sphere("Tank_BottomCavitySphere",
                                           0.,
                                           GetBottomCavityRadius(),
                                           0.,
                                           360. * deg,
                                           0.,
                                           180. * deg);

    tank_solid = new G4SubtractionSolid("Tank",
                                        tank_box,
                                        bottomCavitySphere,
                                        nullptr,
                                        G4ThreeVector(0., 0., -fTank_z));
  }
  else if (fDimpleEnabled) {
    ValidateDimpleConfiguration();

    auto dimpleSphere = new G4Sphere("Tank_DimpleSphere",
                                     0.,
                                     fDimpleRadius,
                                     0.,
                                     360. * deg,
                                     0.,
                                     180. * deg);

    tank_solid = new G4SubtractionSolid("Tank",
                                        tank_box,
                                        dimpleSphere,
                                        nullptr,
                                        G4ThreeVector(0., 0., -fTank_z));
  }

  fTank_LV = new G4LogicalVolume(tank_solid, fTankMaterial, "Tank");

  const auto layerCount = GetStackLayerCount();
  for (G4int layer = 0; layer < layerCount; ++layer) {
    const auto center = fStackEnabled ? GetStackTileCenterZ(layer) : 0.;
    auto placement = new G4PVPlacement(nullptr,
                                       G4ThreeVector(0., 0., center),
                                       fTank_LV,
                                       "Tank",
                                       fWorld_LV,
                                       false,
                                       layer,
                                       fStackEnabled);
    fTanks.push_back(placement);
    if (layer == 0) {
      fTank = placement;
    }
  }

  if (fAbsorberEnabled) {
    ValidateAbsorberConfiguration();

    auto absorberBox =
      new G4Box("SteelAbsorber_Box", fAbsorber_x, fAbsorber_y, fAbsorber_z);
    fAbsorber_LV =
      new G4LogicalVolume(absorberBox, fAbsorberMaterial, "SteelAbsorber");

    auto absorberVis = new G4VisAttributes(G4Colour(0.45, 0.45, 0.50, 0.75));
    absorberVis->SetForceSolid(true);
    fAbsorber_LV->SetVisAttributes(absorberVis);

    for (G4int layer = 0; layer < layerCount; ++layer) {
      const auto center = fStackEnabled
        ? GetStackSteelCenterZ(layer)
        : GetAbsorberCenterZ();
      auto placement = new G4PVPlacement(nullptr,
                                         G4ThreeVector(0., 0., center),
                                         fAbsorber_LV,
                                         "SteelAbsorber",
                                         fWorld_LV,
                                         false,
                                         layer,
                                         true);
      fAbsorbers.push_back(placement);
      if (layer == 0) {
        fAbsorber = placement;
      }
    }

    G4cout << "Realistic-neutron absorber: material="
           << fAbsorberMaterial->GetName()
           << ", density=" << fAbsorberMaterial->GetDensity() / (g / cm3)
           << " g/cm3, full_size="
           << 2. * fAbsorber_x / mm << " x "
           << 2. * fAbsorber_y / mm << " x "
           << 2. * fAbsorber_z / mm << " mm, center_z="
           << (fStackEnabled ? GetStackSteelCenterZ(0) : GetAbsorberCenterZ()) / mm
           << " mm, tile_gap=0 mm, layers=" << layerCount << G4endl;
  }

  if (fGreaseEnabled) {
    ValidateGreaseConfiguration();

    G4VSolid* greaseSolid = nullptr;
    G4ThreeVector greasePos;

    if (fDimpleEnabled) {
      G4double sipmHx = 0.0;
      G4double sipmHy = 0.0;
      G4double sipmHz = 0.0;
      G4ThreeVector sipmPos;
      ComputeSiPMPlacement(sipmHx, sipmHy, sipmHz, sipmPos);

      const G4double localBottom = sipmPos.z() + sipmHz + fTank_z;
      const G4double clipHz = 0.5 * (fDimpleRadius - localBottom);
      const G4double clipCenterZ = localBottom + clipHz;

      auto greaseSphere = new G4Sphere("Grease_DimpleSphere",
                                        0.,
                                        fDimpleRadius,
                                        0.,
                                        360. * deg,
                                        0.,
                                        180. * deg);
      auto greaseClip = new G4Box("Grease_DimpleClip",
                                   0.5 * GetGreaseActiveU(),
                                   0.5 * GetGreaseActiveV(),
                                   clipHz);
      greaseSolid = new G4IntersectionSolid("Grease_DimpleGap",
                                             greaseSphere,
                                             greaseClip,
                                             nullptr,
                                             G4ThreeVector(0., 0., clipCenterZ));
      greasePos = G4ThreeVector(0., 0., -fTank_z);
    }
    else {
      G4double greaseHx = 0.0;
      G4double greaseHy = 0.0;
      G4double greaseHz = 0.0;
      ComputeGreasePlacement(greaseHx, greaseHy, greaseHz, greasePos);
      greaseSolid = new G4Box("Grease_Box", greaseHx, greaseHy, greaseHz);
    }

    fGrease_LV = new G4LogicalVolume(greaseSolid, fGreaseMaterial, "Grease");

    auto greaseVis = new G4VisAttributes(G4Colour(0.0, 0.8, 1.0, 0.35));
    greaseVis->SetForceSolid(true);
    fGrease_LV->SetVisAttributes(greaseVis);

    fGrease = new G4PVPlacement(nullptr,
                                greasePos,
                                fGrease_LV,
                                "Grease",
                                fWorld_LV,
                                false,
                                0,
                                true);
  }

  // The single- or multi-SiPM steel-module layout.
  ValidateSiPMLayout();
  G4double sipmHx = 0.0;
  G4double sipmHy = 0.0;
  G4double sipmHz = 0.0;
  G4ThreeVector sipmPos;

  const auto sipmLocalPositions = GetSiPMLocalPositions();
  ComputeSiPMPlacementFor(fSiPMFace,
                          sipmLocalPositions.front(),
                          sipmHx,
                          sipmHy,
                          sipmHz,
                          sipmPos);

  auto sipm_box = new G4Box("SiPM_Box", sipmHx, sipmHy, sipmHz);

  fSiPM_LV = new G4LogicalVolume(sipm_box, fSiPMMaterial, "SiPM");

  // Visual attributes of SiPM
  auto sipmVis = new G4VisAttributes(G4Colour(0.0, 1.0, 0.0, 0.9));
  sipmVis->SetForceSolid(true);
  fSiPM_LV->SetVisAttributes(sipmVis);

  for (G4int layer = 0; layer < layerCount; ++layer) {
    for (std::size_t index = 0; index < sipmLocalPositions.size(); ++index) {
      G4double placementHx = 0.0;
      G4double placementHy = 0.0;
      G4double placementHz = 0.0;
      G4ThreeVector placementPos;
      ComputeSiPMPlacementFor(fSiPMFace,
                              sipmLocalPositions[index],
                              placementHx,
                              placementHy,
                              placementHz,
                              placementPos);
      if (fStackEnabled) {
        placementPos.setZ(placementPos.z() + GetStackTileCenterZ(layer));
      }
      const G4int copyNumber = fStackEnabled
        ? GetSensorCopyStride() * layer + static_cast<G4int>(index)
        : static_cast<G4int>(index);

      auto placement = new G4PVPlacement(nullptr,
                                         placementPos,
                                         fSiPM_LV,
                                         "SiPM",
                                         fWorld_LV,
                                         false,
                                         copyNumber,
                                         true);  // overlap check
      fSiPMs.push_back(placement);
      if (layer == 0 && index == 0) {
        fSiPM = placement;
      }
      G4cout << "SiPM placement: layout=" << fSiPMLayout
             << ", layer=" << layer
             << ", local_sensor=" << index
             << ", copy=" << copyNumber
             << ", face=" << fSiPMFace
             << ", local=" << sipmLocalPositions[index] / mm
             << " mm, world=" << placementPos / mm << " mm" << G4endl;
    }
  }


  // ------------- Surface --------------

  G4LogicalBorderSurface* surface = nullptr;
  for (G4int layer = 0; layer < layerCount; ++layer) {
    const auto name = "TankToWorldSurface_" + std::to_string(layer);
    auto layerSurface =
      new G4LogicalBorderSurface(name, fTanks[layer], world_PV, fSurface);
    if (layer == 0) {
      surface = layerSurface;
    }
  }

  // The tile remains painted/wrapped where it touches the steel. Border
  // surfaces are ordered, so this explicitly covers photons leaving the tile
  // toward the absorber while the existing tile-to-world boundary remains.
  if (fAbsorberEnabled) {
    for (G4int layer = 0; layer < layerCount; ++layer) {
      new G4LogicalBorderSurface(
        "TankToUpstreamSteelSurface_" + std::to_string(layer),
        fTanks[layer],
        fAbsorbers[layer],
        fSurface);
      if (fStackEnabled && !IsStackV2() && layer + 1 < layerCount) {
        new G4LogicalBorderSurface(
          "TankToDownstreamSteelSurface_" + std::to_string(layer),
          fTanks[layer],
          fAbsorbers[layer + 1],
          fSurface);
      }
    }
  }

  auto opticalSurface =
    dynamic_cast<G4OpticalSurface*>(surface->GetSurface(fTank, world_PV)->GetSurfaceProperty());
  G4cout << "******  opticalSurface->DumpInfo:" << G4endl;
  if (opticalSurface) {
    opticalSurface->DumpInfo();
  }
  G4cout << "******  end of opticalSurface->DumpInfo" << G4endl;

  ValidateW08PhotonLossConfiguration();
  return world_PV;
}

void DetectorConstruction::SetW08PhotonLossEnabled(G4bool enabled)
{
  // A diagnostics flag is not a geometry/physics modification. The UI command
  // is PreInit-only so the action/ntuple layout is fixed before initialization.
  fW08PhotonLossEnabled = enabled;
  G4cout << "W08 photon-loss diagnostics " << (enabled ? "enabled" : "disabled") << G4endl;
}

W08GeometrySnapshot DetectorConstruction::GetW08GeometrySnapshot() const
{
  W08GeometrySnapshot result;
  result.enabled = fW08PhotonLossEnabled;
  result.dimple = fDimpleEnabled;
  result.worldPV = fWorld;
  result.tilePV = fTank;
  result.sipmPV = fSiPM;
  RequireW08(fWorld && fTank && fSiPM, "Geometry snapshot requested before construction.");
  if (!fWorld || !fTank || !fSiPM) return result;
  const auto* worldBox = dynamic_cast<const G4Box*>(fWorld->GetLogicalVolume()->GetSolid());
  const auto* sipmBox = dynamic_cast<const G4Box*>(fSiPM->GetLogicalVolume()->GetSolid());
  RequireW08(worldBox && sipmBox, "World and SiPM must be unrotated boxes.");
  if (!worldBox || !sipmBox) return result;
  const auto* tileSolid = fTank->GetLogicalVolume()->GetSolid();
  const auto* subtraction = dynamic_cast<const G4SubtractionSolid*>(tileSolid);
  const auto* tileBox = dynamic_cast<const G4Box*>(
    subtraction ? subtraction->GetConstituentSolid(0) : tileSolid);
  RequireW08(tileBox != nullptr, "The tile must be a box or a subtraction from that box.");
  if (!tileBox) return result;
  result.worldHalfSize = G4ThreeVector(worldBox->GetXHalfLength(), worldBox->GetYHalfLength(),
                                     worldBox->GetZHalfLength());
  result.tileHalfSize = G4ThreeVector(tileBox->GetXHalfLength(), tileBox->GetYHalfLength(),
                                    tileBox->GetZHalfLength());
  result.tileCenter = fTank->GetObjectTranslation();
  result.sipmHalfSize = G4ThreeVector(sipmBox->GetXHalfLength(), sipmBox->GetYHalfLength(),
                                    sipmBox->GetZHalfLength());
  result.sipmCenter = fSiPM->GetObjectTranslation();
  result.dimpleCenter = result.tileCenter + G4ThreeVector(0., 0., -result.tileHalfSize.z());
  if (fDimpleEnabled) {
    const auto* displaced = subtraction
      ? dynamic_cast<const G4DisplacedSolid*>(subtraction->GetConstituentSolid(1)) : nullptr;
    const auto* sphere = displaced
      ? dynamic_cast<const G4Sphere*>(displaced->GetConstituentMovedSolid()) : nullptr;
    RequireW08(sphere != nullptr, "The dimple must subtract a displaced sphere.");
    if (!sphere) return result;
    RequireW08(W08Equal(sphere->GetInnerRadius() / mm, 0.) &&
                 W08Equal(sphere->GetStartPhiAngle(), 0.) &&
                 W08Equal(sphere->GetDeltaPhiAngle(), twopi) &&
                 W08Equal(sphere->GetStartThetaAngle(), 0.) &&
                 W08Equal(sphere->GetDeltaThetaAngle(), pi),
               "The subtracted dimple primitive must be a complete solid sphere.");
    result.dimpleCenter = result.tileCenter + displaced->GetObjectTranslation();
    result.dimpleRadius = sphere->GetOuterRadius();
  }
  return result;
}

void DetectorConstruction::ValidateW08PhotonLossConfiguration() const
{
  if (!fW08PhotonLossEnabled) return;
  RequireW08(!fBottomCavityEnabled && !fGreaseEnabled && !fAbsorberEnabled && !fStackEnabled,
             "Legacy cavity, grease, absorber, and stack must all be disabled.");
  RequireW08(fSiPMLayout == "single" && fTanks.size() == 1 && fSiPMs.size() == 1 &&
               fAbsorbers.empty() && fGrease == nullptr && fAbsorber == nullptr,
             "Exactly one tile and one SiPM are supported.");
  RequireW08((fSiPMFace == "-Z" || fSiPMFace == "bottom") &&
               W08SamePosition(fSiPMLocalPosition, G4ThreeVector()),
             "The SiPM must use bottom-center placement.");
  if (fDimpleEnabled) {
    RequireW08(fDimpleMode == "hemisphere" && W08Equal(fDimpleRadius / mm, 3.) &&
                 GetEffectiveDimpleSiPMMode() == "opening",
               "The only supported dimple is a strict r=3 mm hemisphere with opening placement.");
  }
  const auto geometry = GetW08GeometrySnapshot();
  if (fDimpleEnabled) {
    RequireW08(W08Equal(geometry.dimpleRadius / mm, 3.) &&
                 W08SamePosition(geometry.dimpleCenter, G4ThreeVector(0., 0., -2.5) * mm),
               "The resolved dimple radius/center differs from W08.");
  }
  RequireW08(W08SamePosition(geometry.worldHalfSize, G4ThreeVector(500., 500., 500.) * mm) &&
               W08SamePosition(fWorld->GetObjectTranslation(), G4ThreeVector()),
             "The World must remain a centered 1000 x 1000 x 1000 mm box.");
  RequireW08(W08SamePosition(geometry.tileHalfSize, G4ThreeVector(50., 50., 2.5) * mm) &&
               W08SamePosition(geometry.tileCenter, G4ThreeVector()),
             "The tile must remain centered with full size 100 x 100 x 5 mm.");
  RequireW08(W08SamePosition(geometry.sipmHalfSize, G4ThreeVector(1., 1., 0.25) * mm) &&
               W08SamePosition(geometry.sipmCenter,
                               G4ThreeVector(0., 0., fDimpleEnabled ? -2.25 : -2.75) * mm),
             "The SiPM must retain its W08 size and resolved world position.");
  RequireW08(fWorld->GetRotation() == nullptr && fTank->GetRotation() == nullptr &&
               fSiPM->GetRotation() == nullptr &&
               fTank->GetMotherLogical() == fWorld_LV && fSiPM->GetMotherLogical() == fWorld_LV &&
               fWorld_LV->GetNoDaughters() == 2 && fTank_LV->GetNoDaughters() == 0 &&
               fSiPM_LV->GetNoDaughters() == 0,
             "Unexpected rotations, hierarchy, or additional volumes.");
  RequireW08(fTank->GetCopyNo() == 0 && fSiPM->GetCopyNo() == 0,
             "The tile and SiPM must retain copy number zero.");
  RequireW08(fTank_LV->GetSolid()->GetEntityType() ==
               (fDimpleEnabled ? "G4SubtractionSolid" : "G4Box"),
             "The tile solid does not match the selected W08 geometry.");

  RequireW08(fSurface && fSurface->GetType() == dielectric_dielectric &&
               fSurface->GetModel() == unified && fSurface->GetFinish() == polished &&
               W08Equal(fSurface->GetSigmaAlpha(), 0.),
             "The shared Tank-to-World boundary must remain unified/dielectric_dielectric/polished.");
  const auto* border = G4LogicalBorderSurface::GetSurface(fTank, fWorld);
  RequireW08(border && border->GetSurfaceProperty() == fSurface,
             "The actual Tank-to-World optical border has changed.");
  RequireW08(G4LogicalBorderSurface::GetSurface(fWorld, fTank) == nullptr &&
               G4LogicalBorderSurface::GetSurface(fTank, fSiPM) == nullptr &&
               G4LogicalBorderSurface::GetSurface(fSiPM, fTank) == nullptr &&
               G4LogicalBorderSurface::GetSurface(fWorld, fSiPM) == nullptr &&
               G4LogicalBorderSurface::GetSurface(fSiPM, fWorld) == nullptr &&
               G4LogicalSkinSurface::GetSurface(fWorld_LV) == nullptr &&
               G4LogicalSkinSurface::GetSurface(fTank_LV) == nullptr &&
               G4LogicalSkinSurface::GetSurface(fSiPM_LV) == nullptr,
             "Additional optical borders or skins are outside W08.");
  CheckW08PropertyNames(fSurface->GetMaterialPropertiesTable(), "Surface", {}, {});

  RequireW08(fTank_LV->GetMaterial() == fTankMaterial &&
               fTankMaterial->GetName() == "G4_PLASTIC_SC_VINYLTOLUENE" &&
               fWorld_LV->GetMaterial() == fWorldMaterial && fWorldMaterial->GetName() == "G4_AIR" &&
               fSiPM_LV->GetMaterial() == fSiPMMaterial && fSiPMMaterial->GetName() == "G4_Si",
             "EJ-200 proxy, air, and silicon material identities must remain unchanged.");
  const auto* tankTable = fTank_LV->GetMaterial()->GetMaterialPropertiesTable();
  const auto* worldTable = fWorld_LV->GetMaterial()->GetMaterialPropertiesTable();
  const auto* sipmTable = fSiPM_LV->GetMaterial()->GetMaterialPropertiesTable();
  CheckW08PropertyNames(tankTable, "EJ-200",
                       {"RINDEX", "GROUPVEL", "ABSLENGTH", "SCINTILLATIONCOMPONENT1"},
                       {"SCINTILLATIONYIELD", "SCINTILLATIONYIELD1", "RESOLUTIONSCALE",
                        "SCINTILLATIONRISETIME1", "SCINTILLATIONTIMECONSTANT1"});
  CheckW08PropertyNames(worldTable, "World", {"RINDEX", "GROUPVEL", "ABSLENGTH"}, {});
  CheckW08PropertyNames(sipmTable, "SiPM", {"RINDEX", "GROUPVEL", "ABSLENGTH"}, {});
  CheckW08ConstantVector(tankTable, "EJ-200", "RINDEX", 1.58);
  CheckW08ConstantVector(tankTable, "EJ-200", "ABSLENGTH", 3800. * mm);
  CheckW08ConstantVector(worldTable, "World", "RINDEX", 1.0003);
  CheckW08ConstantVector(worldTable, "World", "ABSLENGTH", 100. * mm);
  CheckW08ConstantVector(sipmTable, "SiPM", "RINDEX", 4.);
  CheckW08ConstantVector(sipmTable, "SiPM", "ABSLENGTH", 1. * um);
  // GROUPVEL is automatically generated by Geant4 when RINDEX is installed.
  CheckW08ConstantVector(tankTable, "EJ-200", "GROUPVEL", c_light / 1.58);
  CheckW08ConstantVector(worldTable, "World", "GROUPVEL", c_light / 1.0003);
  CheckW08ConstantVector(sipmTable, "SiPM", "GROUPVEL", c_light / 4.);
  CheckW08Constant(tankTable, "SCINTILLATIONYIELD", 10000. / MeV);
  CheckW08Constant(tankTable, "SCINTILLATIONYIELD1", 1.);
  CheckW08Constant(tankTable, "RESOLUTIONSCALE", 1.);
  CheckW08Constant(tankTable, "SCINTILLATIONRISETIME1", 0.9 * ns);
  CheckW08Constant(tankTable, "SCINTILLATIONTIMECONSTANT1", 2.1 * ns);
  RequireW08(W08Equal(fTankMaterial->GetIonisation()->GetBirksConstant(), 0.126 * mm / MeV),
             "EJ-200 Birks constant must remain 0.126 mm/MeV.");

  // Exact spectrum from ej200_sipm_gps_test.mac, in increasing photon energy.
  const std::array<G4double, 18> energiesEV = {
    2.4982, 2.5419, 2.5623, 2.5838, 2.6039, 2.6273,
    2.6495, 2.6823, 2.7196, 2.7700, 2.8458, 2.9033,
    2.9173, 2.9313, 2.9383, 2.9516, 3.0092, 3.1001};
  const std::array<G4double, 18> intensities = {
    0.069, 0.109, 0.134, 0.174, 0.214, 0.274,
    0.335, 0.415, 0.468, 0.582, 0.851, 0.991,
    1.000, 0.985, 0.960, 0.881, 0.352, 0.017};
  const auto* spectrum = tankTable ? tankTable->GetProperty("SCINTILLATIONCOMPONENT1") : nullptr;
  RequireW08(spectrum && spectrum->GetVectorLength() == energiesEV.size(),
             "EJ-200 must retain the complete W08 scintillation spectrum.");
  if (spectrum && spectrum->GetVectorLength() == energiesEV.size()) {
    for (std::size_t i = 0; i < energiesEV.size(); ++i) {
      RequireW08(W08Equal(spectrum->Energy(i) / eV, energiesEV[i]) &&
                   W08Equal((*spectrum)[i], intensities[i]),
                 "EJ-200 spectrum differs from W08 at point " + std::to_string(i));
    }
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetSurfaceSigmaAlpha(G4double v)
{
  fSurface->SetSigmaAlpha(v);
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Surface sigma alpha set to: " << fSurface->GetSigmaAlpha() << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetSurfacePolish(G4double v)
{
  fSurface->SetPolish(v);
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Surface polish set to: " << fSurface->GetPolish() << G4endl;
}

void DetectorConstruction::ResetSurfaceMaterialPropertiesTable()
{
  delete fSurfaceMPT;
  fSurfaceMPT = new G4MaterialPropertiesTable();
  fSurface->SetMaterialPropertiesTable(fSurfaceMPT);
}

void DetectorConstruction::SetSurfacePreset(const G4String& preset)
{
  ResetSurfaceMaterialPropertiesTable();
  fSurface->SetModel(unified);
  fSurface->SetType(dielectric_dielectric);

  const auto addConstantReflectivity = [&]() {
    const G4int nEntries = 2;
    G4double photonEnergy[nEntries] = {2.0 * eV, 3.3 * eV};
    G4double reflectivity[nEntries] = {0.95, 0.95};
    fSurfaceMPT->AddProperty("REFLECTIVITY", photonEnergy, reflectivity, nEntries);
  };

  if (preset == "polished") {
    fSurface->SetFinish(polished);
    fSurface->SetSigmaAlpha(0.0);
  }
  else if (preset == "ground") {
    fSurface->SetFinish(ground);
    fSurface->SetSigmaAlpha(0.2);
  }
  else if (preset == "wrapped") {
    fSurface->SetFinish(polishedfrontpainted);
    fSurface->SetSigmaAlpha(0.0);
    addConstantReflectivity();
  }
  else if (preset == "polishedfrontpainted") {
    fSurface->SetFinish(polishedfrontpainted);
    fSurface->SetSigmaAlpha(0.0);
  }
  else if (preset == "groundfrontpainted") {
    fSurface->SetFinish(groundfrontpainted);
    fSurface->SetSigmaAlpha(0.2);
  }
  else if (preset == "polishedbackpainted") {
    fSurface->SetFinish(polishedbackpainted);
    fSurface->SetSigmaAlpha(0.0);
  }
  else if (preset == "groundbackpainted") {
    fSurface->SetFinish(groundbackpainted);
    fSurface->SetSigmaAlpha(0.2);
  }
  else {
    G4ExceptionDescription msg;
    msg << "Invalid surface preset: " << preset
        << ". Use polished, ground, wrapped, polishedfrontpainted, "
        << "groundfrontpainted, polishedbackpainted, or groundbackpainted.";
    G4Exception("DetectorConstruction::SetSurfacePreset",
                "OpNovice2_Surface_001",
                FatalException,
                msg);
  }

  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "Surface preset applied: " << preset
         << " (model=unified, type=dielectric_dielectric, finish="
         << fSurface->GetFinish() << ", sigma_alpha="
         << fSurface->GetSigmaAlpha() << ")" << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddTankMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  fTankMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the box is now: " << G4endl;
  fTankMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddWorldMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  fWorldMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the world is now: " << G4endl;
  fWorldMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddSurfaceMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  if (fSurfaceMPT->GetProperty(prop) != nullptr) {
    fSurfaceMPT->RemoveProperty(prop);
  }
  fSurfaceMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the surface is now: " << G4endl;
  fSurfaceMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddTankMPC(const G4String& prop, G4double v)
{
  fTankMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the box is now: " << G4endl;
  fTankMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddWorldMPC(const G4String& prop, G4double v)
{
  fWorldMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the world is now: " << G4endl;
  fWorldMPT->DumpTable();
  G4cout << "............." << G4endl;
}
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddSurfaceMPC(const G4String& prop, G4double v)
{
  if (fSurfaceMPT->ConstPropertyExists(prop)) {
    fSurfaceMPT->RemoveConstProperty(prop);
  }
  fSurfaceMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the surface is now: " << G4endl;
  fSurfaceMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddGreaseMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  if (fGreaseMPT->GetProperty(prop) != nullptr) {
    fGreaseMPT->RemoveProperty(prop);
  }
  fGreaseMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the grease is now: " << G4endl;
  fGreaseMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddGreaseMPC(const G4String& prop, G4double v)
{
  if (fGreaseMPT->ConstPropertyExists(prop)) {
    fGreaseMPT->RemoveConstProperty(prop);
  }
  fGreaseMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the grease is now: " << G4endl;
  fGreaseMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetWorldMaterial(const G4String& mat)
{
  G4Material* pmat = G4NistManager::Instance()->FindOrBuildMaterial(mat);
  if (pmat && fWorldMaterial != pmat) {
    fWorldMaterial = pmat;
    if (fWorld_LV) {
      fWorld_LV->SetMaterial(fWorldMaterial);
      fWorldMaterial->SetMaterialPropertiesTable(fWorldMPT);
    }
    G4RunManager::GetRunManager()->PhysicsHasBeenModified();
    G4cout << "World material set to " << fWorldMaterial->GetName() << G4endl;
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetTankMaterial(const G4String& mat)
{
  G4Material* pmat = G4NistManager::Instance()->FindOrBuildMaterial(mat);
  if (pmat && fTankMaterial != pmat) {
    fTankMaterial = pmat;
    if (fTank_LV) {
      fTank_LV->SetMaterial(fTankMaterial);
      fTankMaterial->SetMaterialPropertiesTable(fTankMPT);
      fTankMaterial->GetIonisation()->SetBirksConstant(0.126 * mm / MeV);
    }
    G4RunManager::GetRunManager()->PhysicsHasBeenModified();
    G4cout << "Tank material set to " << fTankMaterial->GetName() << G4endl;
  }
}

void DetectorConstruction::SetTankSize(const G4ThreeVector& fullSize)
{
  if (fullSize.x() <= 0. || fullSize.y() <= 0. || fullSize.z() <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid tank full size: " << fullSize / cm
        << " cm. All dimensions must be positive.";
    G4Exception("DetectorConstruction::SetTankSize",
                "OpNovice2_Tank_001",
                FatalException,
                msg);
  }

  fTank_x = 0.5 * fullSize.x();
  fTank_y = 0.5 * fullSize.y();
  fTank_z = 0.5 * fullSize.z();
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Tank full size set to "
         << 2. * fTank_x / cm << " x "
         << 2. * fTank_y / cm << " x "
         << 2. * fTank_z / cm << " cm" << G4endl;
}

void DetectorConstruction::SetTankSizePreset(const G4String& preset)
{
  if (preset == "5x5x0p4") {
    SetTankSize(G4ThreeVector(5. * cm, 5. * cm, 0.4 * cm));
  }
  else if (preset == "5x5x0p8") {
    SetTankSize(G4ThreeVector(5. * cm, 5. * cm, 0.8 * cm));
  }
  else if (preset == "5x5x1p6") {
    SetTankSize(G4ThreeVector(5. * cm, 5. * cm, 1.6 * cm));
  }
  else if (preset == "10x10x0p4") {
    SetTankSize(G4ThreeVector(10. * cm, 10. * cm, 0.4 * cm));
  }
  else if (preset == "10x10x0p8") {
    SetTankSize(G4ThreeVector(10. * cm, 10. * cm, 0.8 * cm));
  }
  else if (preset == "10x10x1p6") {
    SetTankSize(G4ThreeVector(10. * cm, 10. * cm, 1.6 * cm));
  }
  else {
    G4ExceptionDescription msg;
    msg << "Unknown tank size preset: " << preset
        << ". Use 5x5x0p4, 5x5x0p8, 5x5x1p6, "
        << "10x10x0p4, 10x10x0p8, or 10x10x1p6.";
    G4Exception("DetectorConstruction::SetTankSizePreset",
                "OpNovice2_Tank_002",
                FatalException,
                msg);
  }
}

void DetectorConstruction::SetAbsorberEnabled(G4bool enabled)
{
  fAbsorberEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Steel absorber enabled set to "
         << (fAbsorberEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetAbsorberSize(const G4ThreeVector& fullSize)
{
  if (fullSize.x() <= 0. || fullSize.y() <= 0. || fullSize.z() <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid absorber full size: " << fullSize / mm
        << " mm. All dimensions must be positive.";
    G4Exception("DetectorConstruction::SetAbsorberSize",
                "OpNovice2_Absorber_001",
                FatalException,
                msg);
  }

  fAbsorber_x = 0.5 * fullSize.x();
  fAbsorber_y = 0.5 * fullSize.y();
  fAbsorber_z = 0.5 * fullSize.z();
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Steel absorber full size set to "
         << 2. * fAbsorber_x / mm << " x "
         << 2. * fAbsorber_y / mm << " x "
         << 2. * fAbsorber_z / mm << " mm" << G4endl;
}

void DetectorConstruction::SetStackEnabled(G4bool enabled)
{
  fStackEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "Longitudinal stack enabled set to "
         << (fStackEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetStackLayers(G4int layers)
{
  if (layers <= 0) {
    G4ExceptionDescription msg;
    msg << "Invalid stack layer count: " << layers << ". Use a positive integer.";
    G4Exception("DetectorConstruction::SetStackLayers",
                "OpNovice2_Stack_001",
                FatalException,
                msg);
  }
  fStackLayers = layers;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "Longitudinal stack layer count set to " << fStackLayers << G4endl;
}

void DetectorConstruction::SetStackModel(const G4String& model)
{
  if (model != "v1" && model != "v2") {
    G4Exception("DetectorConstruction::SetStackModel", "OpNovice2_Stack_008",
                FatalException, "Stack model must be v1 or v2.");
    return;
  }
  fStackModel = model;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
}

void DetectorConstruction::SetStackReadoutGap(G4double gap)
{
  if (!std::isfinite(gap) || gap < 0.5 * mm) {
    G4Exception("DetectorConstruction::SetStackReadoutGap", "OpNovice2_Stack_009",
                FatalException, "The explicit stack-v2 readout gap must be finite and at least 0.5 mm.");
    return;
  }
  fStackReadoutGap = gap;
  fStackReadoutGapExplicit = true;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
}

void DetectorConstruction::SetStackPhotonAccounting(G4bool enabled)
{
  fStackPhotonAccounting = enabled;
}

G4int DetectorConstruction::GetActiveSensorsPerLayer() const
{
  if (fSiPMLayout == "back-four") return 4;
  if (fSiPMLayout == "back-two" || fSiPMLayout == "edge-two") return 2;
  return 1;
}

G4bool DetectorConstruction::IsSensorCopyActive(G4int copyNumber) const
{
  if (copyNumber < 0) return false;
  if (!fStackEnabled) return copyNumber < GetActiveSensorsPerLayer();
  return copyNumber < GetSensorCopyStride() * fStackLayers &&
         copyNumber % GetSensorCopyStride() < GetActiveSensorsPerLayer();
}

G4ThreeVector DetectorConstruction::GetSiPMHalfSize() const
{
  G4double hx = 0., hy = 0., hz = 0.;
  G4ThreeVector center;
  ComputeSiPMPlacementFor(fSiPMFace, GetSiPMLocalPositions().front(), hx, hy, hz, center);
  return {hx, hy, hz};
}

G4ThreeVector DetectorConstruction::GetSiPMWorldPosition(G4int copyNumber) const
{
  for (const auto* sensor : fSiPMs) {
    if (sensor->GetCopyNo() == copyNumber) return sensor->GetObjectTranslation();
  }
  G4Exception("DetectorConstruction::GetSiPMWorldPosition", "OpNovice2_Stack_010",
              FatalException, "Requested inactive or unconstructed SiPM copy.");
  return {};
}

G4double DetectorConstruction::GetStackSteelCenterZ(G4int layer) const
{
  const G4double moduleLength = GetStackModulePitch();
  return 0.5 * GetStackLength() - layer * moduleLength - fAbsorber_z;
}

G4double DetectorConstruction::GetStackTileCenterZ(G4int layer) const
{
  const G4double moduleLength = GetStackModulePitch();
  return 0.5 * GetStackLength() - layer * moduleLength
         - 2. * fAbsorber_z - fTank_z;
}

G4int DetectorConstruction::GetTileLayer(const G4VPhysicalVolume* volume) const
{
  const auto found = std::find(fTanks.begin(), fTanks.end(), volume);
  return found == fTanks.end()
    ? -1
    : static_cast<G4int>(std::distance(fTanks.begin(), found));
}

G4int DetectorConstruction::GetAbsorberLayer(const G4VPhysicalVolume* volume) const
{
  const auto found = std::find(fAbsorbers.begin(), fAbsorbers.end(), volume);
  return found == fAbsorbers.end()
    ? -1
    : static_cast<G4int>(std::distance(fAbsorbers.begin(), found));
}

G4int DetectorConstruction::GetSensorLayer(G4int copyNumber) const
{
  if (!fStackEnabled) {
    return copyNumber >= 0 ? 0 : -1;
  }
  return IsSensorCopyActive(copyNumber)
    ? copyNumber / GetSensorCopyStride()
    : -1;
}

G4int DetectorConstruction::GetSensorLocalIndex(G4int copyNumber) const
{
  if (!fStackEnabled) {
    return copyNumber;
  }
  return IsSensorCopyActive(copyNumber)
    ? copyNumber % GetSensorCopyStride()
    : -1;
}

void DetectorConstruction::SetBottomCavityEnabled(G4bool enabled)
{
  fBottomCavityEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Tank bottom cavity set to "
         << (fBottomCavityEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetDimpleEnabled(G4bool enabled)
{
  fDimpleEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple set to "
         << (fDimpleEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetDimpleRadius(G4double radius)
{
  if (radius <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid dimple radius: " << radius / mm
        << " mm. Radius must be positive.";
    G4Exception("DetectorConstruction::SetDimpleRadius",
                "OpNovice2_Dimple_012",
                FatalException,
                msg);
  }

  fDimpleRadius = radius;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple radius set to " << fDimpleRadius / mm << " mm" << G4endl;
}

void DetectorConstruction::SetDimpleMode(const G4String& mode)
{
  if (mode != "hemisphere") {
    G4ExceptionDescription msg;
    msg << "Invalid dimple mode: " << mode
        << ". Only hemisphere is supported in Week 8.1.";
    G4Exception("DetectorConstruction::SetDimpleMode",
                "OpNovice2_Dimple_013",
                FatalException,
                msg);
  }

  fDimpleMode = mode;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple mode set to " << fDimpleMode << G4endl;
}

void DetectorConstruction::SetDimpleSiPMMode(const G4String& mode)
{
  if (mode != "surface" && mode != "opening") {
    G4ExceptionDescription msg;
    msg << "Invalid dimple SiPM mode: " << mode << ". Use surface or opening.";
    G4Exception("DetectorConstruction::SetDimpleSiPMMode",
                "OpNovice2_Dimple_014",
                FatalException,
                msg);
  }

  fDimpleSiPMMode = mode;
  fDimpleSiPMModeExplicit = true;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple SiPM mode set to " << fDimpleSiPMMode << G4endl;
}

G4double DetectorConstruction::GetBottomCavityRadius() const
{
  const G4double tankThickness = 2. * fTank_z;
  const G4double minimumTopThickness = 0.25 * tankThickness;
  return tankThickness - minimumTopThickness;
}

G4String DetectorConstruction::GetEffectiveDimpleSiPMMode() const
{
  if (fDimpleSiPMModeExplicit && fSiPMCavityModeExplicit &&
      fDimpleSiPMMode != fSiPMCavityMode) {
    G4ExceptionDescription msg;
    msg << "Conflicting dimple SiPM placement modes: /opnovice2/dimple/sipmMode="
        << fDimpleSiPMMode << " but legacy /opnovice2/sipm/cavityMode="
        << fSiPMCavityMode << ". Use one mode, or set both to the same value.";
    G4Exception("DetectorConstruction::GetEffectiveDimpleSiPMMode",
                "OpNovice2_Dimple_002",
                FatalException,
                msg);
  }

  if (!fDimpleSiPMModeExplicit && fSiPMCavityModeExplicit) {
    return fSiPMCavityMode;
  }

  return fDimpleSiPMMode;
}

G4double DetectorConstruction::GetSiPMFootprintCornerRadius(G4double u,
                                                            G4double v,
                                                            G4double hu,
                                                            G4double hv) const
{
  const G4double cornerRadii[] = {
    std::sqrt((u - hu) * (u - hu) + (v - hv) * (v - hv)),
    std::sqrt((u - hu) * (u - hu) + (v + hv) * (v + hv)),
    std::sqrt((u + hu) * (u + hu) + (v - hv) * (v - hv)),
    std::sqrt((u + hu) * (u + hu) + (v + hv) * (v + hv))
  };

  G4double rMax = cornerRadii[0];
  for (G4int i = 1; i < 4; ++i) {
    rMax = std::max(rMax, cornerRadii[i]);
  }
  return rMax;
}

void DetectorConstruction::ValidateAbsorberConfiguration() const
{
  if (fAbsorber_x < fTank_x || fAbsorber_y < fTank_y) {
    G4ExceptionDescription msg;
    msg << "The steel absorber must cover the tile transversely. "
        << "absorber full size=" << 2. * fAbsorber_x / mm << " x "
        << 2. * fAbsorber_y / mm << " mm, tile full size="
        << 2. * fTank_x / mm << " x " << 2. * fTank_y / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateAbsorberConfiguration",
                "OpNovice2_Absorber_002",
                FatalException,
                msg);
  }

  if (fAbsorber_x >= fExpHall_x || fAbsorber_y >= fExpHall_y ||
      GetAbsorberUpstreamFaceZ() >= fExpHall_z) {
    G4ExceptionDescription msg;
    msg << "The steel absorber does not fit strictly inside the world. "
        << "absorber half transverse size=" << fAbsorber_x / mm << " x "
        << fAbsorber_y / mm << " mm, upstream face z="
        << GetAbsorberUpstreamFaceZ() / mm << " mm; world half size="
        << fExpHall_x / mm << " x " << fExpHall_y / mm << " x "
        << fExpHall_z / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateAbsorberConfiguration",
                "OpNovice2_Absorber_003",
                FatalException,
                msg);
  }

  if (fSiPMFace == "+Z" || fSiPMFace == "top") {
    G4ExceptionDescription msg;
    msg << "A +Z SiPM would overlap the zero-gap steel absorber. "
        << "Attach the SiPM to another tile face; the neutron study presets "
        << "use -Z or a lateral face.";
    G4Exception("DetectorConstruction::ValidateAbsorberConfiguration",
                "OpNovice2_Absorber_004",
                FatalException,
                msg);
  }
}

void DetectorConstruction::ValidateStackConfiguration() const
{
  const G4double safety = 1.e-6 * mm;
  if (fStackLayers != 10) {
    G4ExceptionDescription msg;
    msg << "steel-module-stack-v1 requires exactly 10 layers; current value is "
        << fStackLayers << ".";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_002",
                FatalException,
                msg);
  }
  if (!fAbsorberEnabled) {
    G4ExceptionDescription msg;
    msg << "The longitudinal stack requires the SAE-304 absorber.";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_003",
                FatalException,
                msg);
  }
  if (fBottomCavityEnabled || fDimpleEnabled || fGreaseEnabled) {
    G4ExceptionDescription msg;
    msg << "The longitudinal stack supports only the undimpled zero-gap "
        << "coupling proxy; cavity, dimple, and explicit grease must be disabled.";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_004",
                FatalException,
                msg);
  }
  if (IsStackV2()) {
    const auto equalLength = [](G4double value, G4double expected) {
      return std::isfinite(value) && std::abs(value - expected) <= 1.e-9 * mm;
    };
    const auto require = [](G4bool condition, const char* message) {
      if (!condition) G4Exception("DetectorConstruction::ValidateStackConfiguration",
                                 "OpNovice2_StackV2_001", FatalException, message);
    };
    require(fStackReadoutGapExplicit && std::isfinite(fStackReadoutGap) &&
              fStackReadoutGap >= 0.5 * mm,
            "stack-v2 requires an explicit finite readoutGap >= 0.5 mm; no production default is selected.");
    require(equalLength(fTank_x, 50. * mm) && equalLength(fTank_y, 50. * mm) &&
              equalLength(fAbsorber_x, 250. * mm) && equalLength(fAbsorber_y, 250. * mm) &&
              equalLength(fAbsorber_z, 20. * mm),
            "stack-v2 requires 100x100 mm tiles and 500x500x40 mm steel slabs.");
    G4bool acceptedThickness = false;
    for (const auto thickness : {4., 8., 12., 16., 20., 24.}) {
      acceptedThickness = acceptedThickness || equalLength(2. * fTank_z, thickness * mm);
    }
    require(acceptedThickness, "stack-v2 supports tile thicknesses 4, 8, 12, 16, 20, 24 mm only.");
    require(equalLength(fSiPMActiveU, 2.4 * mm) && equalLength(fSiPMActiveV, 2.4 * mm) &&
              equalLength(fSiPMThickness, 0.5 * mm),
            "stack-v2 requires 2.4x2.4x0.5 mm SiPM proxies.");
    require(fSiPMLocalPosition.mag() <= 1.e-9 * mm,
            "stack-v2 requires the registered fixed layout positions, not a local-position override.");
    const G4bool side = fSiPMLayout == "edge-two" &&
                         (fSiPMFace == "+X" || fSiPMFace == "right");
    const G4bool back = (fSiPMLayout == "single" || fSiPMLayout == "back-two" ||
                         fSiPMLayout == "back-four") &&
                         (fSiPMFace == "-Z" || fSiPMFace == "bottom");
    require(side || back, "stack-v2 supports back-center, back-two, back-four on -Z or edge-two on +X.");
    require(0.5 * GetStackCoreLength() + 1.5 * mm < fExpHall_z - safety &&
              0.5 * GetStackCoreLength() + (back ? fSiPMThickness : 0.) < fExpHall_z - safety &&
              fAbsorber_x < fExpHall_x - safety && fAbsorber_y < fExpHall_y - safety,
            "stack-v2 steel, source clearance, or final back-SiPM protrusion exceeds the World.");
  }
  else if (fSiPMLayout != "edge-two" ||
      (fSiPMFace != "+X" && fSiPMFace != "right")) {
    G4ExceptionDescription msg;
    msg << "The longitudinal stack requires edge-two on the +X face.";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_005",
                FatalException,
                msg);
  }
  if (0.5 * GetStackLength() >= fExpHall_z - safety ||
      fTank_x + fSiPMThickness >= fExpHall_x - safety ||
      fTank_y >= fExpHall_y - safety) {
    G4ExceptionDescription msg;
    msg << "The longitudinal stack does not fit strictly inside the world. "
        << "stack length=" << GetStackLength() / mm
        << " mm, tile/SiPM x extent=" << (fTank_x + fSiPMThickness) / mm
        << " mm, world half sizes=" << fExpHall_x / mm << " x "
        << fExpHall_y / mm << " x " << fExpHall_z / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_006",
                FatalException,
                msg);
  }
}

void DetectorConstruction::ValidateDimpleConfiguration() const
{
  const G4double safety = 1.e-6 * mm;

  if (fDimpleMode != "hemisphere") {
    G4ExceptionDescription msg;
    msg << "Invalid dimple mode: " << fDimpleMode
        << ". Only hemisphere is supported in Week 8.1.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_003",
                FatalException,
                msg);
  }

  if (fDimpleRadius <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid dimple radius: " << fDimpleRadius / mm
        << " mm. Radius must be positive.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_004",
                FatalException,
                msg);
  }

  const G4double tankThickness = 2. * fTank_z;
  if (fDimpleRadius >= tankThickness - safety) {
    G4ExceptionDescription msg;
    msg << "Strict hemispherical dimple radius is too large for this tile. "
        << "radius=" << fDimpleRadius / mm
        << " mm, tile thickness=" << tankThickness / mm
        << " mm. Radius must be smaller than thickness with safety margin.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_005",
                FatalException,
                msg);
  }

  if (fSiPMFace != "-Z" && fSiPMFace != "bottom") {
    G4ExceptionDescription msg;
    msg << "Week 8.1 dimple supports only bottom-center -Z SiPM placement. "
        << "Current /opnovice2/sipm/face is " << fSiPMFace << ".";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_006",
                FatalException,
                msg);
  }

  if (std::abs(fSiPMLocalPosition.x()) > safety ||
      std::abs(fSiPMLocalPosition.y()) > safety ||
      std::abs(fSiPMLocalPosition.z()) > safety) {
    G4ExceptionDescription msg;
    msg << "Week 8.1 dimple supports only bottom-center SiPM local position "
        << "0 0 0. Current local position is "
        << fSiPMLocalPosition / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_007",
                FatalException,
                msg);
  }

  const G4double hu = 0.5 * fSiPMActiveU;
  const G4double hv = 0.5 * fSiPMActiveV;
  const G4double rMax = GetSiPMFootprintCornerRadius(0., 0., hu, hv);
  if (rMax >= fDimpleRadius - safety) {
    G4ExceptionDescription msg;
    msg << "SiPM active face does not fit inside the dimple opening. "
        << "farthest corner radius=" << rMax / mm
        << " mm, dimple radius=" << fDimpleRadius / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_008",
                FatalException,
                msg);
  }

  const G4String sipmMode = GetEffectiveDimpleSiPMMode();
  if (sipmMode != "surface" && sipmMode != "opening") {
    G4ExceptionDescription msg;
    msg << "Unknown dimple SiPM mode: " << sipmMode
        << ". Use surface or opening.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_009",
                FatalException,
                msg);
  }

  if (sipmMode == "opening") {
    const G4double dimpleTopAtFootprint =
      -fTank_z + std::sqrt(fDimpleRadius * fDimpleRadius - rMax * rMax);
    const G4double topFace = -fTank_z + fSiPMThickness;
    if (topFace >= dimpleTopAtFootprint - safety) {
      G4ExceptionDescription msg;
      msg << "Dimple opening placement would overlap EJ-200. "
          << "top face z=" << topFace / mm
          << " mm, dimple clearance z=" << dimpleTopAtFootprint / mm << " mm.";
      G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                  "OpNovice2_Dimple_010",
                  FatalException,
                  msg);
    }
  }
}

G4double DetectorConstruction::GetGreaseActiveU() const
{
  return fGreaseActiveU > 0.0 ? fGreaseActiveU : fSiPMActiveU;
}

G4double DetectorConstruction::GetGreaseActiveV() const
{
  return fGreaseActiveV > 0.0 ? fGreaseActiveV : fSiPMActiveV;
}

void DetectorConstruction::ValidateGreaseConfiguration() const
{
  const G4double safety = 1.e-6 * mm;

  if (fSiPMFace != "-Z" && fSiPMFace != "bottom") {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease coupling currently supports only -Z SiPM placement. "
        << "Current /opnovice2/sipm/face is " << fSiPMFace << ".";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_001",
                FatalException,
                msg);
  }

  if (fBottomCavityEnabled) {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease coupling cannot be combined with bottom cavity geometry.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_002",
                FatalException,
                msg);
  }

  if (std::abs(fSiPMLocalPosition.x()) > safety ||
      std::abs(fSiPMLocalPosition.y()) > safety ||
      std::abs(fSiPMLocalPosition.z()) > safety) {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease coupling currently supports only bottom-center SiPM local "
        << "position 0 0 0. Current local position is "
        << fSiPMLocalPosition / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_003",
                FatalException,
                msg);
  }

  if (!fDimpleEnabled && fGreaseThickness <= 0.0) {
    G4ExceptionDescription msg;
    msg << "EJ-550 flat-pad thickness must be positive when grease coupling is enabled.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_004",
                FatalException,
                msg);
  }

  const G4double greaseU = GetGreaseActiveU();
  const G4double greaseV = GetGreaseActiveV();
  if (greaseU <= 0.0 || greaseV <= 0.0) {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease active size must be positive. Current size is "
        << greaseU / mm << " x " << greaseV / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_005",
                FatalException,
                msg);
  }

  if (0.5 * greaseU > fTank_x + safety || 0.5 * greaseV > fTank_y + safety) {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease pad does not fit on the tile bottom face. "
        << "grease=" << greaseU / mm << " x " << greaseV / mm
        << " mm, tile=" << (2.0 * fTank_x) / mm << " x "
        << (2.0 * fTank_y) / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_006",
                FatalException,
                msg);
  }

  if (fDimpleEnabled) {
    if (fGreaseThickness > safety) {
      G4ExceptionDescription msg;
      msg << "EJ-550 dimple-gap coupling derives its thickness from the curved "
          << "dimple-to-SiPM gap; do not set /opnovice2/grease/thickness.";
      G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                  "OpNovice2_Grease_007",
                  FatalException,
                  msg);
    }

    if (greaseU > fSiPMActiveU + safety || greaseV > fSiPMActiveV + safety) {
      G4ExceptionDescription msg;
      msg << "EJ-550 dimple-gap footprint cannot exceed the SiPM active face. "
          << "grease=" << greaseU / mm << " x " << greaseV / mm
          << " mm, SiPM=" << fSiPMActiveU / mm << " x "
          << fSiPMActiveV / mm << " mm.";
      G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                  "OpNovice2_Grease_008",
                  FatalException,
                  msg);
    }

    const G4double greaseCornerRadius =
      std::sqrt(0.25 * greaseU * greaseU + 0.25 * greaseV * greaseV);
    if (greaseCornerRadius >= fDimpleRadius - safety) {
      G4ExceptionDescription msg;
      msg << "EJ-550 dimple-gap footprint does not fit inside the dimple. "
          << "corner radius=" << greaseCornerRadius / mm
          << " mm, dimple radius=" << fDimpleRadius / mm << " mm.";
      G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                  "OpNovice2_Grease_009",
                  FatalException,
                  msg);
    }

    G4double sipmHx = 0.0;
    G4double sipmHy = 0.0;
    G4double sipmHz = 0.0;
    G4ThreeVector sipmPos;
    ComputeSiPMPlacement(sipmHx, sipmHy, sipmHz, sipmPos);
    const G4double localBottom = sipmPos.z() + sipmHz + fTank_z;
    if (localBottom >= fDimpleRadius - safety) {
      G4ExceptionDescription msg;
      msg << "EJ-550 dimple-gap has no positive clearance above the SiPM. "
          << "SiPM top relative to dimple center=" << localBottom / mm
          << " mm, dimple radius=" << fDimpleRadius / mm << " mm.";
      G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                  "OpNovice2_Grease_010",
                  FatalException,
                  msg);
    }
  }
}

void DetectorConstruction::ComputeGreasePlacement(G4double& hx,
                                                  G4double& hy,
                                                  G4double& hz,
                                                  G4ThreeVector& pos) const
{
  hx = 0.5 * GetGreaseActiveU();
  hy = 0.5 * GetGreaseActiveV();
  hz = 0.5 * fGreaseThickness;
  pos = G4ThreeVector(0.0, 0.0, -fTank_z - hz);
}


void DetectorConstruction::ComputeSiPMPlacement(G4double& hx,
                                                G4double& hy,
                                                G4double& hz,
                                                G4ThreeVector& pos) const
{
  ComputeSiPMPlacementFor(fSiPMFace, fSiPMLocalPosition, hx, hy, hz, pos);
}

void DetectorConstruction::ComputeSiPMPlacementFor(
  const G4String& face,
  const G4ThreeVector& localPosition,
  G4double& hx,
  G4double& hy,
  G4double& hz,
  G4ThreeVector& pos) const
{
  const G4double hu = 0.5 * fSiPMActiveU;
  const G4double hv = 0.5 * fSiPMActiveV;
  const G4double ht = 0.5 * fSiPMThickness;

  const G4double u = localPosition.x();
  const G4double v = localPosition.y();

  if (face == "+X" || face == "right") {
    hx = ht;
    hy = hu;
    hz = hv;
    pos = G4ThreeVector(fTank_x + ht, u, v);
  }
  else if (face == "-X" || face == "left") {
    hx = ht;
    hy = hu;
    hz = hv;
    pos = G4ThreeVector(-fTank_x - ht, u, v);
  }
  else if (face == "+Y" || face == "back") {
    hx = hu;
    hy = ht;
    hz = hv;
    pos = G4ThreeVector(u, fTank_y + ht, v);
  }
  else if (face == "-Y" || face == "front") {
    hx = hu;
    hy = ht;
    hz = hv;
    pos = G4ThreeVector(u, -fTank_y - ht, v);
  }
  else if (face == "+Z" || face == "top") {
    hx = hu;
    hy = hv;
    hz = ht;
    pos = G4ThreeVector(u, v, fTank_z + ht);
  }
  else if (face == "-Z" || face == "bottom") {
    hx = hu;
    hy = hv;
    hz = ht;
    if (fDimpleEnabled) {
      const G4double safety = 1.e-6 * mm;
      const G4double rMax = GetSiPMFootprintCornerRadius(u, v, hu, hv);
      const G4double dimpleTopAtFootprint =
        -fTank_z + std::sqrt(fDimpleRadius * fDimpleRadius - rMax * rMax);
      const G4String sipmMode = GetEffectiveDimpleSiPMMode();

      if (sipmMode == "surface") {
        pos = G4ThreeVector(u, v, dimpleTopAtFootprint - ht - safety);
      }
      else if (sipmMode == "opening") {
        pos = G4ThreeVector(u, v, -fTank_z + ht);
      }
      else {
        G4ExceptionDescription msg;
        msg << "Unknown dimple SiPM mode: " << sipmMode
            << ". Use surface or opening.";
        G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                    "OpNovice2_Dimple_011",
                    FatalException,
                    msg);
      }
    }
    else {
      const G4double greaseOffset = fGreaseEnabled ? fGreaseThickness : 0.0;
      pos = G4ThreeVector(u, v, -fTank_z - greaseOffset - ht);
    }
  }
  else if (face == "bottomCavity") {
    hx = hu;
    hy = hv;
    hz = ht;

    if (!fBottomCavityEnabled) {
      G4ExceptionDescription msg;
      msg << "SiPM face bottomCavity requires /opnovice2/tank/bottomCavity true.";
      G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                  "OpNovice2_SiPM_002",
                  FatalException,
                  msg);
    }

    const G4double cavityRadius = GetBottomCavityRadius();
    const G4double rMax = GetSiPMFootprintCornerRadius(u, v, hu, hv);
    const G4double safety = 1.e-6 * mm;

    if (rMax >= cavityRadius - safety) {
      G4ExceptionDescription msg;
      msg << "SiPM footprint does not fit inside the bottom cavity opening. "
          << "farthest corner radius=" << rMax / mm
          << " mm, cavity radius=" << cavityRadius / mm << " mm.";
      G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                  "OpNovice2_SiPM_003",
                  FatalException,
                  msg);
    }

    const G4double cavityTopAtFootprint =
      -fTank_z + std::sqrt(cavityRadius * cavityRadius - rMax * rMax);

    if (fSiPMCavityMode == "surface") {
      pos = G4ThreeVector(u, v, cavityTopAtFootprint - ht - safety);
    }
    else if (fSiPMCavityMode == "opening") {
      const G4double topFace = -fTank_z + fSiPMThickness;
      if (topFace >= cavityTopAtFootprint - safety) {
        G4ExceptionDescription msg;
        msg << "SiPM opening placement would overlap EJ-200. "
            << "top face z=" << topFace / mm
            << " mm, cavity clearance z=" << cavityTopAtFootprint / mm << " mm.";
        G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                    "OpNovice2_SiPM_004",
                    FatalException,
                    msg);
      }
      pos = G4ThreeVector(u, v, -fTank_z + ht);
    }
    else {
      G4ExceptionDescription msg;
      msg << "Unknown SiPM cavity mode: " << fSiPMCavityMode
          << ". Use surface or opening.";
      G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                  "OpNovice2_SiPM_005",
                  FatalException,
                  msg);
    }
  }
  else {
    G4ExceptionDescription msg;
    msg << "Unknown SiPM face: " << face
        << ". Use +X, -X, +Y, -Y, +Z, -Z, bottomCavity.";
    G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                "OpNovice2_SiPM_001",
                FatalException,
                msg);
  }
}

std::vector<G4ThreeVector> DetectorConstruction::GetSiPMLocalPositions() const
{
  if (fSiPMLayout == "edge-two") {
    const G4double offset = 25. * mm;
    return {
      G4ThreeVector(-offset, 0., 0.),
      G4ThreeVector(offset, 0., 0.)
    };
  }
  if (fSiPMLayout == "back-four") {
    const G4double offset = 25. * mm;
    return {
      G4ThreeVector(-offset, -offset, 0.),
      G4ThreeVector(-offset, offset, 0.),
      G4ThreeVector(offset, -offset, 0.),
      G4ThreeVector(offset, offset, 0.)
    };
  }
  if (fSiPMLayout == "back-two") {
    const G4double offset = 25. * mm;
    return {G4ThreeVector(-offset, -offset, 0.), G4ThreeVector(offset, offset, 0.)};
  }
  return {fSiPMLocalPosition};
}

void DetectorConstruction::ValidateSiPMLayout() const
{
  if (fSiPMLayout == "single") {
    return;
  }
  if (fSiPMLayout == "edge-two") {
    if (fSiPMFace != "+X" && fSiPMFace != "right") {
      G4ExceptionDescription msg;
      msg << "The edge-two SiPM layout requires face +X; current face is "
          << fSiPMFace << ".";
      G4Exception("DetectorConstruction::ValidateSiPMLayout",
                  "OpNovice2_SiPM_012",
                  FatalException,
                  msg);
    }
    if (fBottomCavityEnabled || fDimpleEnabled || fGreaseEnabled) {
      G4ExceptionDescription msg;
      msg << "The edge-two SiPM layout supports only the undimpled zero-gap "
          << "coupling proxy; bottom cavity, dimple, and explicit grease must be disabled.";
      G4Exception("DetectorConstruction::ValidateSiPMLayout",
                  "OpNovice2_SiPM_013",
                  FatalException,
                  msg);
    }

    const G4double offset = 25. * mm;
    const G4double safety = 1.e-6 * mm;
    if (offset + 0.5 * fSiPMActiveU >= fTank_y - safety ||
        0.5 * fSiPMActiveV >= fTank_z - safety) {
      G4ExceptionDescription msg;
      msg << "The edge-two SiPM footprints do not fit on the +X tile face. "
          << "tile full side size=" << 2. * fTank_y / mm << " x "
          << 2. * fTank_z / mm << " mm, SiPM active size="
          << fSiPMActiveU / mm << " x " << fSiPMActiveV / mm
          << " mm, center offset=25 mm.";
      G4Exception("DetectorConstruction::ValidateSiPMLayout",
                  "OpNovice2_SiPM_014",
                  FatalException,
                  msg);
    }
    return;
  }
  if (fSiPMLayout != "back-four" && fSiPMLayout != "back-two") {
    G4ExceptionDescription msg;
    msg << "Unknown SiPM layout: " << fSiPMLayout
        << ". Use single, edge-two, or back-four.";
    G4Exception("DetectorConstruction::ValidateSiPMLayout",
                "OpNovice2_SiPM_007",
                FatalException,
                msg);
    return;
  }

  if (fSiPMFace != "-Z" && fSiPMFace != "bottom") {
    G4ExceptionDescription msg;
    msg << "The back-four SiPM layout requires face -Z; current face is "
        << fSiPMFace << ".";
    G4Exception("DetectorConstruction::ValidateSiPMLayout",
                "OpNovice2_SiPM_008",
                FatalException,
                msg);
  }
  if (fBottomCavityEnabled || fDimpleEnabled || fGreaseEnabled) {
    G4ExceptionDescription msg;
    msg << "The back-four SiPM layout supports only the undimpled zero-gap "
        << "coupling proxy; bottom cavity, dimple, and explicit grease must be disabled.";
    G4Exception("DetectorConstruction::ValidateSiPMLayout",
                "OpNovice2_SiPM_009",
                FatalException,
                msg);
  }

  const G4double offset = 25. * mm;
  const G4double safety = 1.e-6 * mm;
  if (offset + 0.5 * fSiPMActiveU >= fTank_x - safety ||
      offset + 0.5 * fSiPMActiveV >= fTank_y - safety) {
    G4ExceptionDescription msg;
    msg << "The back-four SiPM footprints do not fit on the -Z tile face. "
        << "tile full size=" << 2. * fTank_x / mm << " x "
        << 2. * fTank_y / mm << " mm, SiPM active size="
        << fSiPMActiveU / mm << " x " << fSiPMActiveV / mm
        << " mm, center offset=25 mm.";
    G4Exception("DetectorConstruction::ValidateSiPMLayout",
                "OpNovice2_SiPM_010",
                FatalException,
                msg);
  }
}

void DetectorConstruction::SetSiPMLayout(const G4String& layout)
{
  if (layout != "single" && layout != "edge-two" && layout != "back-four" && layout != "back-two") {
    G4ExceptionDescription msg;
    msg << "Invalid SiPM layout: " << layout
        << ". Use single, edge-two, back-two, or back-four.";
    G4Exception("DetectorConstruction::SetSiPMLayout",
                "OpNovice2_SiPM_011",
                FatalException,
                msg);
  }

  fSiPMLayout = layout;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "SiPM layout set to " << fSiPMLayout << G4endl;
}

void DetectorConstruction::SetSiPMFace(const G4String& face)
{
  fSiPMFace = face;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM face set to " << fSiPMFace << G4endl;
}

void DetectorConstruction::SetSiPMCavityMode(const G4String& mode)
{
  if (mode != "surface" && mode != "opening") {
    G4ExceptionDescription msg;
    msg << "Invalid SiPM cavity mode: " << mode << ". Use surface or opening.";
    G4Exception("DetectorConstruction::SetSiPMCavityMode",
                "OpNovice2_SiPM_006",
                FatalException,
                msg);
  }

  fSiPMCavityMode = mode;
  fSiPMCavityModeExplicit = true;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM cavity mode set to " << fSiPMCavityMode << G4endl;
}

void DetectorConstruction::SetSiPMLocalPosition(const G4ThreeVector& pos)
{
  fSiPMLocalPosition = pos;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM local position set to "
         << fSiPMLocalPosition / cm << " cm" << G4endl;
}

void DetectorConstruction::SetSiPMSize(const G4ThreeVector& size)
{
  fSiPMActiveU = size.x();
  fSiPMActiveV = size.y();
  fSiPMThickness = size.z();

  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM size set to activeU="
         << fSiPMActiveU / mm << " mm, activeV="
         << fSiPMActiveV / mm << " mm, thickness="
         << fSiPMThickness / mm << " mm" << G4endl;
}

void DetectorConstruction::SetGreaseEnabled(G4bool enabled)
{
  fGreaseEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "EJ-550 grease coupling "
         << (fGreaseEnabled ? "enabled" : "disabled") << G4endl;
}

void DetectorConstruction::SetGreaseThickness(G4double thickness)
{
  if (thickness <= 0.0) {
    G4ExceptionDescription msg;
    msg << "Invalid EJ-550 grease thickness: " << thickness / mm
        << " mm. Thickness must be positive.";
    G4Exception("DetectorConstruction::SetGreaseThickness",
                "OpNovice2_Grease_007",
                FatalException,
                msg);
  }

  fGreaseThickness = thickness;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "EJ-550 grease thickness set to "
         << fGreaseThickness / mm << " mm" << G4endl;
}

void DetectorConstruction::SetGreaseSize(const G4ThreeVector& size)
{
  if (size.x() <= 0.0 || size.y() <= 0.0) {
    G4ExceptionDescription msg;
    msg << "Invalid EJ-550 grease size: " << size.x() / mm << " x "
        << size.y() / mm << " mm. Active dimensions must be positive.";
    G4Exception("DetectorConstruction::SetGreaseSize",
                "OpNovice2_Grease_008",
                FatalException,
                msg);
  }

  fGreaseActiveU = size.x();
  fGreaseActiveV = size.y();
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "EJ-550 grease size set to activeU="
         << fGreaseActiveU / mm << " mm, activeV="
         << fGreaseActiveV / mm << " mm" << G4endl;
}
